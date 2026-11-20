"""Where smeltery looks for Temporal by default, and its task queues.

Plain constants, importing nothing: the CLI's argparse defaults and the dashboard read them without importing
temporalio (slow to import), and `smeltery.tasks` and `smeltery.temporal_run` (which do import it) re-export them.
"""

DEFAULT_HOST = "127.0.0.1:7233"
DEFAULT_NAMESPACE = "default"
#: `smeltery task-worker`'s queue: the shell tasks of `smeltery.tasks`.
TASKS_QUEUE = "smeltery-tasks"
#: `smeltery temporal-worker`'s queue: the SCF legs of `smeltery run --temporal`.
PIPELINE_QUEUE = "smeltery-pipeline"
#: How to start a local dev server, for the message a worker or run prints when it cannot reach one.
DOCKER_COMMAND = (
    "docker run -d --name temporal-dev -p 7233:7233 -p 8233:8233 "
    "temporalio/temporal:latest server start-dev --ip 0.0.0.0"
)
