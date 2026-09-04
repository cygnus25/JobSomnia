from unittest.mock import patch

import jobscraper.schedule as schedule


def test_run_scheduled_prints_summary_on_success(capsys):
    mock_result = {
        "raw_total": 12,
        "new_count": 4,
        "above_threshold": 2,
        "top_jobs": [
            {"title": "Senior Backend Engineer", "company": "Acme Corp",
             "score": 92, "url": "https://example.com/jobs/backend-engineer"},
            {"title": "Data Analyst", "company": "Beta LLC",
             "score": 81, "url": "https://example.com/jobs/data-analyst"},
        ],
        "run_dir": "output/runs/2024-01-01_08-00-00",
    }

    with patch.object(schedule, "run_pipeline", return_value=mock_result):
        schedule.run_scheduled()

    out = capsys.readouterr().out
    assert "Senior Backend Engineer" in out
    assert "Acme Corp" in out
    assert "92" in out
    assert "https://example.com/jobs/backend-engineer" in out
    assert "Raw jobs scraped: 12" in out
    assert "New jobs: 4" in out
    assert "Run time:" in out


def test_run_scheduled_prints_fallback_line_when_no_top_jobs(capsys):
    mock_result = {"raw_total": 5, "new_count": 0, "above_threshold": 0, "top_jobs": []}

    with patch.object(schedule, "run_pipeline", return_value=mock_result):
        schedule.run_scheduled()

    out = capsys.readouterr().out
    assert "No new jobs scored this run." in out


def test_run_scheduled_exits_1_on_pipeline_failure(capsys):
    with patch.object(schedule, "run_pipeline", side_effect=RuntimeError("boom")), \
         patch("sys.exit") as mock_exit:
        schedule.run_scheduled()

    mock_exit.assert_called_once_with(1)
    out = capsys.readouterr().out
    assert "ERROR" in out
    assert "boom" in out
