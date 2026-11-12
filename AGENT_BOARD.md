# Multi-agent collaboration protocol ? REQUIRED

Shared task board at:
D:\GrowthEngineeringVenture\15_INTEGRATION_WORKBENCH_20261111\AGENT_BOARD\board.py

Local dashboard: http://127.0.0.1:4366/
All agents use ONE canonical Git main checkout. Concurrent edits allowed ONLY for
different file scopes with successful atomic story claims. No worktrees or branches.

Before editing:
    python -B D:/GrowthEngineeringVenture/15_INTEGRATION_WORKBENCH_20261111/AGENT_BOARD/board.py status
    python -B D:/GrowthEngineeringVenture/15_INTEGRATION_WORKBENCH_20261111/AGENT_BOARD/board.py feed
    python -B D:/GrowthEngineeringVenture/15_INTEGRATION_WORKBENCH_20261111/AGENT_BOARD/board.py claim MF-001 --agent claude

During work, post progress and heartbeats. Never edit another story's reserved paths.
For full PostgreSQL verification and Git commit, acquire integration gate:
    python -B D:/GrowthEngineeringVenture/15_INTEGRATION_WORKBENCH_20261111/AGENT_BOARD/board.py gate --agent claude

While gate held, every other agent stops source edits (read-only code review is fine).
Run full disposable DB suite, commit only YOUR paths, submit real SHA:
    python -B D:/GrowthEngineeringVenture/15_INTEGRATION_WORKBENCH_20261111/AGENT_BOARD/board.py submit MF-001 --agent claude --commit abcdef1 --notes "What was verified"
    python -B D:/GrowthEngineeringVenture/15_INTEGRATION_WORKBENCH_20261111/AGENT_BOARD/board.py ungate --agent claude

A different agent reviews and APPROVES or requests changes through board.py review.
See AGENT_BOARD/README.md for command examples and all rules.
