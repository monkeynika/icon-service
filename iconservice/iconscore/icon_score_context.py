# -*- coding: utf-8 -*-

# Copyright 2017-2018 theloop Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import threading
from enum import IntEnum, unique

from ..base.address import Address
from ..base.block import Block
from ..base.message import Message
from ..base.transaction import Transaction
from ..base.exception import ExceptionCode, IconException
from ..base.exception import IconScoreBaseException, PayableException, ExternalException
from ..icx.icx_engine import IcxEngine
from ..database.batch import BlockBatch, TransactionBatch
from .icon_score_result import IconBlockResult

from typing import Optional
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .icon_score_base import IconScoreBase


_thread_local_data = threading.local()


class ContextContainer(object):
    """ContextContainer mixin

    Every class inherit ContextContainer can share IconScoreContext instance
    in the current thread.
    """
    @staticmethod
    def _get_context() -> 'IconScoreContext':
        return getattr(_thread_local_data, 'context', None)

    @staticmethod
    def _put_context(value: 'IconScoreContext') -> None:
        setattr(_thread_local_data, 'context', value)

    @staticmethod
    def _delete_context(context: 'IconScoreContext') -> None:
        """Delete the context of the current thread
        """
        if context is not _thread_local_data.context:
            raise IconException(
                ExceptionCode.INTERNAL_ERROR,
                'Critical error in context management')

        del _thread_local_data.context


class ContextGetter(object):
    """The class which refers to IconScoreContext should inherit ContextGetter
    """
    @property
    def _context(self):
        return getattr(_thread_local_data, 'context', None)


@unique
class IconScoreContextType(IntEnum):
    GENESIS = 0
    INVOKE = 1
    QUERY = 2


class IconScoreContext(object):
    """Contains the useful information to process user's jsonrpc request
    """
    icx: 'IcxEngine' = None
    icon_score_mapper: 'IconScoreInfoMapper' = None

    def __init__(self,
                 context_type: IconScoreContextType = IconScoreContextType.QUERY,
                 block: Block = None,
                 tx: Transaction = None,
                 msg: Message = None,
                 block_batch: BlockBatch = None,
                 tx_batch: TransactionBatch = None) -> None:
        """Constructor

        :param context_type: IconScoreContextType.GENESIS, INVOKE, QUERY
        :param block:
        :param tx: initial transaction info
        :param msg: message call info
        :param block_batch:
        :param tx_batch:
        """
        self.type: IconScoreContextType = context_type
        self.block = block
        self.tx = tx
        self.msg = msg
        self.block_batch = None
        self.tx_batch = None
        self.block_result = None

        self.__msg_stack = []

    @property
    def readonly(self):
        return self.type == IconScoreContextType.QUERY

    def gasleft(self) -> int:
        """Returns the amount of gas left

        If gasleft is zero before tx handling is complete,
        rollback all changed state for the tx
        Consumed gas doesn't need to be paid back to tx owner.

        :return: the amount of gas left
        """
        raise NotImplementedError()

    def get_balance(self, address: Address) -> int:
        """Returns the icx balance of context owner (icon score)

        :return: the icx amount of balance
        """
        return self.icx.get_balance(self, address)

    def transfer(self, addr_from: Address, addr_to: Address, amount: int) -> bool:
        """Transfer the amount of icx to the account indicated by 'to'.

        If failed, an exception will be raised.

        :param addr_from:
        :param addr_to:
        :param amount: icx amount in loop (1 icx == 1e18 loop)
        """
        return self.icx.transfer(self, addr_from, addr_to, amount)

    def send(self, addr_from: Address, addr_to: Address, amount: int) -> bool:
        """Send the amount of icx to the account indicated by 'to'.

        :param addr_from:
        :param addr_to: recipient address
        :param amount: icx amount in loop (1 icx == 1e18 loop)
        :return: True(success), False(failure)
        """
        ret = True

        try:
            self.icx.transfer(self, addr_from, addr_to, amount)
        except:
            ret = False

        return ret

    def call(self, addr_from: Address,
             addr_to: Address, func_name: str, kw_params: dict) -> None:
        """Call the functions provided by other icon scores.

        :param addr_from:
        :param addr_to:
        :param func_name:
        :param args:
        :param kwargs:
        :return:
        """
        self.__msg_stack.append(self.msg)

        self.msg = Message(sender=addr_from)
        icon_score = self.icon_score_mapper.get_icon_score(addr_to)

        ret = call_method(icon_score=icon_score, func_name=func_name,
                          addr_from=addr_from, kw_params=kw_params)

        self.msg = self.__msg_stack.pop()

        return ret

    def selfdestruct(self, recipient: Address) -> None:
        """Destroy the current icon score, sending its funds to the given address

        :param recipient: fund recipient
        """

    def revert(self, message: str = None) -> None:
        """Abort execution and revert state changes

        :param message: error log message
        """

    def clear(self) -> None:
        """Set instance member variables to None
        """
        self.block = None
        self.tx = None
        self.msg = None
        self.block_batch = None
        self.tx_batch = None
        self.__msg_stack.clear()

    def commit(self) -> None:
        """Write changed states in block_batch to StateDB

        It is called on write_precommit message from loopchain
        """
        if self.readonly:
            raise IconException(
                ExceptionCode.INTERNAL_ERROR,
                'Commit is not possbile on readonly context')

        if self.block_batch is None:
            raise IconException(
                ExceptionCode.INTERNAL_ERROR, 'Commit failure: BlockBatch is None')

        block_batch = self.block_batch
        for icon_score_address in block_batch:
            info = self.icon_score_mapper.get(icon_score_address)
            if info is None:
                raise IconScoreBaseException('IconScoreInfo is None')
            info.icon_score.db.write_batch(block_batch)

    def rollback(self) -> None:
        """Rollback changed states in block_batch

        It will be done to clear data in block_batch in IconScoreContextFactory.destroy()
        """
        # Nothing to do


class IconScoreContextFactory(object):
    """IconScoreContextFactory
    """
    def __init__(self, max_size: int) -> None:
        """Constructor
        """
        self._lock = threading.Lock()
        self._queue = []
        self._max_size = max_size

    def create(self, context_type: IconScoreContextType) -> IconScoreContext:
        context = None

        with self._lock:
            if len(self._queue) > 0:
                context = self._queue.pop()
                context.type = context_type
            else:
                context = IconScoreContext(context_type)

        return context

    def destroy(self, context: IconScoreContext) -> None:
        with self._lock:
            if len(self._queue) < self._max_size:
                context.clear()
                self._queue.append(context)


def call_method(icon_score: 'IconScoreBase', func_name: str, kw_params: dict,
                addr_from: Optional[Address]=None) -> object:

    if icon_score is None:
        raise IconScoreBaseException('score is None')

    if __check_myself(addr_from, icon_score.address):
        raise IconScoreBaseException("call function myself")

    try:
        return icon_score.call_method(func_name, kw_params)
    except (PayableException, ExternalException):
        call_fallback(icon_score)
        return None
    except Exception:
        raise


def call_fallback(icon_score: 'IconScoreBase') -> None:
    call_fallback_func = getattr(icon_score, '_IconScoreBase__call_fallback')
    call_fallback_func()


def __check_myself(addr_from: Optional[Address], addr_to: Address) -> bool:
    return addr_from == addr_to


def is_context_readonly(context: 'IconScoreContext') -> bool:
    return context is not None and context.readonly
