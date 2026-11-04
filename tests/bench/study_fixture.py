from smeltery.study.condition import grid
from smeltery.study.step import FunctionStep, Study


def make_parent(x):
    return x


step = FunctionStep(
    name="parent",
    version="1",
    _needs=(),
    _provides="parent",
    fn=lambda inputs, cond, ctx: make_parent(cond),
)
study = Study(name="demo", steps=[step], conditions=grid(x=[1, 2]))
