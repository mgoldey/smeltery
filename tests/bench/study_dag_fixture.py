"""A three-step study with real dependencies, for drawing a DAG: parent -> poses -> score, over two conditions."""

from smeltery.study.condition import grid
from smeltery.study.step import FunctionStep, Study

parent = FunctionStep(name="parent", version="1", _needs=(), _provides="parent", fn=lambda inputs, cond, ctx: dict(cond))
poses = FunctionStep(
    name="poses", version="1", _needs=("parent",), _provides="poses", fn=lambda inputs, cond, ctx: [inputs["parent"]]
)
score = FunctionStep(
    name="score", version="1", _needs=("poses", "parent"), _provides="score", fn=lambda inputs, cond, ctx: len(inputs["poses"])
)
study = Study(name="dag-demo", steps=[parent, poses, score], conditions=grid(x=[1, 2]))
