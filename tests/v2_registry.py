"""Small card-driven registry double for isolated orchestration tests."""

from resagent.capabilities import CapabilityError
from resagent.models import Producer


class TestCapabilityRegistry:
    __test__ = False

    owners = {
        "modify_code": Producer.CodingAgent,
        "reproduce_experiment": Producer.ReproAgent,
        "execute_experiment": Producer.ReproAgent,
        "analyze_results": Producer.ExpAgent,
        "search_literature": Producer.ExpAgent,
        "ask_user": Producer.ResAgent,
    }

    def resolve(self, capability: str) -> Producer:
        try:
            return self.owners[capability]
        except KeyError as exc:
            raise CapabilityError(f"unknown capability {capability!r}") from exc

    def controller_summary(self) -> str:
        return "\n".join(
            f"- {capability} -> {producer.value}"
            for capability, producer in self.owners.items()
        )


def make_registry() -> TestCapabilityRegistry:
    return TestCapabilityRegistry()
