import asyncio

import pytest

from src.core.orchestrator import Orchestrator
from src.core.result import AnalysisResult, Direction


class FakeAgent:
    def __init__(self, name: str, delay: float = 0.0):
        self.name = name
        self.description = f"{name} test agent"
        self.delay = delay

    async def run(self, target: str, timeframe: str):
        await asyncio.sleep(self.delay)
        return AnalysisResult(
            agent_name=self.name,
            target=target,
            timeframe=timeframe,
            direction=Direction.NEUTRAL,
            confidence=0.5,
            reasoning="ok",
        )


@pytest.mark.asyncio
async def test_run_selected_reports_each_agent_completion():
    orchestrator = Orchestrator()
    orchestrator.register(FakeAgent("agent-a", delay=0.01))
    orchestrator.register(FakeAgent("agent-b", delay=0.0))
    events = []

    results = await orchestrator.run_selected(
        "000001",
        "短期(1周)",
        agent_names=["agent-a", "agent-b"],
        on_agent_done=lambda name, completed, total: events.append(
            (name, completed, total)
        ),
    )

    assert len(results) == 2
    assert len(events) == 2
    assert sorted(event[0] for event in events) == ["agent-a", "agent-b"]
    assert sorted(event[1] for event in events) == [1, 2]
    assert {event[2] for event in events} == {2}


@pytest.mark.asyncio
async def test_run_selected_can_force_sequential_agent_execution():
    orchestrator = Orchestrator()
    orchestrator.max_concurrent_agents = 1
    active = 0
    max_active = 0

    class TrackedAgent(FakeAgent):
        async def run(self, target: str, timeframe: str):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            try:
                return await super().run(target, timeframe)
            finally:
                active -= 1

    orchestrator.register(TrackedAgent("agent-a", delay=0.01))
    orchestrator.register(TrackedAgent("agent-b", delay=0.01))
    events = []

    results = await orchestrator.run_selected(
        "000001",
        "短期(1周)",
        agent_names=["agent-a", "agent-b"],
        on_agent_done=lambda name, completed, total: events.append(
            (name, completed, total)
        ),
    )

    assert len(results) == 2
    assert max_active == 1
    assert events == [("agent-a", 1, 2), ("agent-b", 2, 2)]
