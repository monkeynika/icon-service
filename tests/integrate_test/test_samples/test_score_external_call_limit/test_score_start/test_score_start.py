from iconservice import *


class Interface(InterfaceScore):
    @interface
    def invoke(self, _to: Address, _name: str) -> None: pass

    @interface
    def query(self, _to: Address, _name: str) -> None: pass


class TestScoreStart(IconScoreBase):

    def __init__(self, db: IconScoreDatabase) -> None:
        super().__init__(db)

    def on_install(self) -> None:
        super().on_install()

    def on_update(self) -> None:
        super().on_update()

    @external
    def invokeCircular(self, _to: Address, _name: str) -> None:
        self.call(_to, _name, {'index': 0})

    @external(readonly=True)
    def queryCircular(self, _to: Address, _name: str) -> int:
        ret = self.call(_to, _name, {})

    @external
    def invoke(self, index: int) -> None:
        print(f'index:{index}')
        self.call(self.msg.sender, 'invoke', {'index': index + 1})

    @external(readonly=True)
    def query(self) -> int:
        ret = self.call(self.msg.sender, 'query', {})


    @external
    def invokeLoop(self, _to: Address, _name: str, _count: int) -> None:
        for i in range(_count):
            self.call(_to, _name, {})

    @external(readonly=True)
    def queryLoop(self, _to: Address, _name: str, _count: int) -> str:
        result: int = 0

        for i in range(_count):
            result += self.call(_to, _name, {})

        return result
