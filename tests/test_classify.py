import yaml

from classify import Detection, _write_back, detect_ats


def test_detect_greenhouse():
    html = '<a href="https://boards.greenhouse.io/examplefirm/jobs/123">Apply</a>'
    d = detect_ats(html)
    assert d.ats_type == "greenhouse"
    assert d.identifier == "examplefirm"


def test_detect_greenhouse_embed():
    html = 'src="https://boards.greenhouse.io/embed/job_board?for=acmelaw"'
    d = detect_ats(html)
    assert d.ats_type == "greenhouse"
    assert d.identifier == "acmelaw"


def test_detect_lever():
    html = '<iframe src="https://jobs.lever.co/coolfirm"></iframe>'
    d = detect_ats(html)
    assert d.ats_type == "lever"
    assert d.identifier == "coolfirm"


def test_detect_workday():
    html = 'window.location="https://latham.wd1.myworkdayjobs.com/en-US/lathamcareers"'
    d = detect_ats(html)
    assert d.ats_type == "workday"
    assert d.identifier == "latham/lathamcareers"
    assert d.workday_host == "latham.wd1.myworkdayjobs.com"


def test_detect_workday_cxs():
    html = 'fetch("https://firm.wd3.myworkdayjobs.com/wday/cxs/firm/External/jobs")'
    d = detect_ats(html)
    assert d.ats_type == "workday"
    assert d.identifier == "firm/External"
    assert d.workday_host == "firm.wd3.myworkdayjobs.com"


def test_detect_other():
    assert detect_ats("powered by symplicity.com").ats_type == "other"
    assert detect_ats("<script src='//x.icims.com/a.js'>").ats_type == "other"


def test_detect_none():
    assert detect_ats("<html>just a page</html>") is None


def test_write_back_preserves_structure(tmp_path):
    src = tmp_path / "firms.yaml"
    src.write_text(
        "# header comment\n"
        "firms:\n"
        '  - name: "Latham & Watkins"\n'
        "    careers_url: https://www.lw.com/en/careers\n"
        "    ats_type: unknown\n"
        "    ats_identifier: null\n"
        "    note: \"keep me\"\n"
    )
    dets = {
        "Latham & Watkins": Detection(
            "workday", identifier="latham/lathamcareers",
            workday_host="latham.wd1.myworkdayjobs.com",
        )
    }
    _write_back(src, dets)
    text = src.read_text()
    assert "# header comment" in text  # comment preserved
    assert 'note: "keep me"' in text   # other fields preserved
    data = yaml.safe_load(text)
    firm = data["firms"][0]
    assert firm["ats_type"] == "workday"
    assert firm["ats_identifier"] == "latham/lathamcareers"
    assert firm["workday_host"] == "latham.wd1.myworkdayjobs.com"
