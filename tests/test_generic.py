from fetchers.generic import _detail_text, extract_jsonld_jobs, extract_microdata_jobs


def test_detail_text_prefers_content_region():
    # Experience text lives in <main>; nav/footer noise is excluded.
    html = (
        "<html><nav>Home 20 years of tradition</nav>"
        "<main><p>Seeking an associate with at least 3 years of experience.</p></main>"
        "<footer>Founded 100 years ago</footer></html>"
    )
    text = _detail_text(html)
    assert "at least 3 years of experience" in text
    assert "tradition" not in text and "Founded" not in text


def test_detail_text_empty():
    assert _detail_text("") == ""

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


# Mirrors the Kilpatrick WordPress card shape: a JobPosting microdata article
# whose title/datePosted are itemprops, whose own URL is a detail-page link, and
# whose only other links are social-share chrome (which must NOT be taken as URL).
_HTML_MICRODATA = """
<article class="job us-tx-dallas laterals litigation" itemscope
         itemtype="https://schema.org/JobPosting">
  <h3 itemprop="title">Litigation Associate (Dallas)</h3>
  <span itemprop="datePosted">July 1, 2026</span>
  <div class="moreshare">
    <a href="https://www.linkedin.com/shareArticle?url=x" class="linkedin">in</a>
    <a href="mailto:?subject=job" class="email">mail</a>
  </div>
  <a href="javascript:shareOption(1)" class="share">Share</a>
  <a href="https://firm.com/open-positions/litigation-associate-dallas/"
     class="apllybutton">Apply</a>
</article>
<article itemscope itemtype="https://schema.org/JobPosting">
  <h3 itemprop="title">Entry-Level Trademark Associate (Atlanta)</h3>
  <span itemprop="datePosted">June 2, 2026</span>
  <a href="https://firm.com/open-positions/trademark-associate-atlanta/"
     class="apllybutton">Apply</a>
</article>
"""


def test_microdata_two_cards():
    jobs = extract_microdata_jobs("Firm", _HTML_MICRODATA, "https://firm.com/open-positions/")
    assert len(jobs) == 2
    a, b = jobs
    assert a.title == "Litigation Associate (Dallas)"
    assert a.posted_date == "July 1, 2026"
    # URL is the detail-page link, NOT the linkedin/mailto share links.
    assert a.url == "https://firm.com/open-positions/litigation-associate-dallas/"
    assert a.job_id == a.url  # per-job URL doubles as the stable id
    assert a.ats == "generic"
    assert b.title == "Entry-Level Trademark Associate (Atlanta)"


def test_microdata_none_returns_empty():
    assert extract_microdata_jobs("Firm", "<html><article>no schema</article></html>", "http://x") == []


def test_microdata_falls_back_to_page_url_and_title_id():
    # A card with no link of its own -> url is the page, id is firm+title.
    html = (
        '<div itemscope itemtype="http://schema.org/JobPosting">'
        '<h2 itemprop="title">Corporate Associate</h2></div>'
    )
    jobs = extract_microdata_jobs("Firm", html, "https://firm.com/careers")
    assert len(jobs) == 1
    assert jobs[0].url == "https://firm.com/careers"
    assert jobs[0].job_id == "Firm:Corporate Associate"
