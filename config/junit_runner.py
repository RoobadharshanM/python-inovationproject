"""A Django test runner that also writes a JUnit XML report.

Jenkins' ``junit`` step expects an XML file.  Django's built-in runners only
print to the console, so without this the build either has to skip test
reporting or parse console text.  Enable it with::

    python manage.py test --testrunner config.junit_runner.JunitXmlRunner

or set ``TEST_RUNNER`` in settings.  Writing the report must never turn a
failing test run into a crash, so every error here is swallowed after being
printed.
"""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from xml.etree import ElementTree

from django.test.runner import DiscoverRunner

DEFAULT_REPORT_PATH = "test-results.xml"


def _resolve_report_path() -> Path:
    configured = os.environ.get("JUNIT_XML_PATH", "").strip()
    return Path(configured or DEFAULT_REPORT_PATH)


class JunitXmlResult(unittest.TextTestResult):
    """Collect per-test results and emit JUnit XML when the run finishes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._records: list[dict[str, object]] = []
        self._suite_start = 0.0
        self._started_at = 0.0

    # -- unittest hooks ----------------------------------------------------
    def startTestRun(self):
        self._suite_start = time.time()
        super().startTestRun()

    def startTest(self, test):
        self._started_at = time.time()
        super().startTest(test)

    def _record(self, test, status: str, detail: str | None = None) -> None:
        self._records.append(
            {
                "classname": f"{type(test).__module__}.{type(test).__qualname__}",
                "name": str(test),
                "status": status,
                "duration": max(0.0, time.time() - self._started_at),
                "detail": detail,
            }
        )

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, "passed")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, "failed", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, "error", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._record(test, "skipped", reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._record(test, "skipped", "expected failure")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._record(test, "passed", "unexpected success")

    def stopTestRun(self):
        total = max(0.0, time.time() - self._suite_start)
        super().stopTestRun()
        self.write_report(total)

    # -- reporting ---------------------------------------------------------
    def write_report(self, total_duration: float) -> None:
        path = _resolve_report_path()
        try:
            suite = ElementTree.Element(
                "testsuite",
                {
                    "name": "django",
                    "tests": str(len(self._records)),
                    "failures": str(sum(1 for r in self._records if r["status"] == "failed")),
                    "errors": str(sum(1 for r in self._records if r["status"] == "error")),
                    "skipped": str(sum(1 for r in self._records if r["status"] == "skipped")),
                    "time": f"{total_duration:.3f}",
                },
            )
            for record in self._records:
                case = ElementTree.SubElement(
                    suite,
                    "testcase",
                    {
                        "classname": str(record["classname"]),
                        "name": str(record["name"]),
                        "time": f"{float(record['duration']):.3f}",
                    },
                )
                status = record["status"]
                if status in {"failed", "error"}:
                    node = ElementTree.SubElement(case, status)
                    node.text = str(record["detail"] or "")
                elif status == "skipped":
                    node = ElementTree.SubElement(case, "skipped")
                    node.text = str(record["detail"] or "")
            path.parent.mkdir(parents=True, exist_ok=True)
            ElementTree.ElementTree(suite).write(
                path, encoding="utf-8", xml_declaration=True
            )
        except Exception as exc:  # pragma: no cover - reporting must not break CI
            print(f"junit: could not write {path}: {exc}")


class JunitXmlRunner(DiscoverRunner):
    """``DiscoverRunner`` that publishes a JUnit XML report for CI.

    Django builds the result class through :meth:`get_resultclass`, so pointing
    that hook at :class:`JunitXmlResult` is all that is required.  Django's own
    result classes are preserved when ``--debug-sql`` or ``--pdb`` is used,
    because those modes stream SQL/tracebacks that are lost if substituted.
    """

    def get_resultclass(self):
        if self.debug_sql or self.pdb:
            return super().get_resultclass()
        return JunitXmlResult