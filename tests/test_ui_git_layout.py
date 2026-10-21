import pytest
from textual.app import App, ComposeResult

from coding_agent.ui.metrics import SystemMetrics, TokenTracker
from coding_agent.ui.widgets.project_panel import ProjectPanel
from coding_agent.ui.widgets.system_panel import SystemPanel


class LayoutTestApp(App):
    def compose(self) -> ComposeResult:
        yield ProjectPanel()
        tracker = TokenTracker()
        metrics = SystemMetrics()
        yield SystemPanel(tracker, metrics)


@pytest.mark.asyncio
async def test_project_panel_buttons_minimized():
    app = LayoutTestApp()
    async with app.run_test():
        p = app.query_one(ProjectPanel)
        btn_new = p.query_one("#btn-new-project")
        btn_open = p.query_one("#btn-open-project")
        assert btn_new.size.height == 1
        assert btn_open.size.height == 1
        # Git indicator removed from left panel
        assert len(p.query("#pgit")) == 0


@pytest.mark.asyncio
async def test_system_panel_git_allocated_space():
    app = LayoutTestApp()
    async with app.run_test():
        s = app.query_one(SystemPanel)
        assert s.query_one("#git-box") is not None
        assert s.query_one("#git-indicator") is not None
        assert s.query_one("#git-changes") is not None
        assert s.query_one("#git-updates") is not None

        s.apply_git_info(
            branch="feature",
            changed=["app.py"],
            untracked=0,
            is_repo=True,
            latest_commit={"hexsha": "abcdef1", "summary": "docs update", "time": "Oct 20 12:00"},
            upstream_status="synced",
        )
        assert "feature" in str(s.query_one("#git-indicator").content)
        assert "app.py" in str(s.query_one("#git-changes").content)
        assert "abcdef1" in str(s.query_one("#git-updates").content)
