from fetchers.generic import extract_jsonld_jobs

_HTML_SINGLE = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting",
 "title":"First-Year Associate","url":"https://firm.com/jobs/1",
 "datePosted":"2026-06-01","identifier":{"@type":"PropertyValue","value":"REQ-1"},
 "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",
   "addressLocality":"New York","addressRegion":"NY"}}}
</script>
</head><body></body></html>
"""

_HTML_GRAPH = """
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
  {"@type":"JobPosting","title":"Entry-Level Associate","url":"https://firm.com/2"},
  {"@type":"Organization","name":"Firm LLP"}
]}
</script>
"""


def test_single_jobposting():
    jobs = extract_jsonld_jobs("Firm", _HTML_SINGLE, "https://firm.com/careers")
    assert len(jobs) == 1
    j = jobs[0]
    assert j.title == "First-Year Associate"
    assert j.job_id == "REQ-1"
    assert j.location == "New York, NY"
    assert j.ats == "generic"


def test_graph_jobposting_only_jobs():
    jobs = extract_jsonld_jobs("Firm", _HTML_GRAPH, "https://firm.com/careers")
    assert len(jobs) == 1
    assert jobs[0].title == "Entry-Level Associate"


def test_no_jsonld_returns_empty():
    assert extract_jsonld_jobs("Firm", "<html>nothing</html>", "http://x") == []
