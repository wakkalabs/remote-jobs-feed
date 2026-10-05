import contextlib
import io
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import tomllib
from unittest.mock import patch

import build_feed


class NewSourceTests(unittest.TestCase):
    def parse_fixture(self, source, payload):
        with patch.object(build_feed, "fetch", return_value=payload):
            return build_feed.SOURCES[source]({}, "test-agent/1.0")

    def test_remote_first_jobs_rss_normalizes_and_keeps_listing_url(self):
        xml = b'''<rss version="2.0"><channel><item>
          <title>Acme: Senior DevOps Engineer</title><link>https://remotefirstjobs.com/jobs/123</link>
          <guid>rfj-123</guid><pubDate>Tue, 02 Jan 2024 03:04:05 GMT</pubDate>
          <description><![CDATA[Remote in US &amp; AWS]]></description><category>DevOps</category>
          <location>United States</location>
        </item></channel></rss>'''
        jobs = self.parse_fixture("remotefirstjobs", xml)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual((job.source, job.id, job.company, job.title),
                         ("remotefirstjobs", "rfj-123", "Acme", "Senior DevOps Engineer"))
        self.assertEqual(job.url, "https://remotefirstjobs.com/jobs/123")
        self.assertEqual(job.location, "United States")
        self.assertIn("AWS", job.description)

    def test_workanywhere_atom_rss_reads_namespaced_links_and_iso_dates(self):
        xml = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
          <id>https://workanywhere.pro/jobs/456</id><title>Senior Platform Engineer at Example Co</title>
          <link rel="alternate" href="https://workanywhere.pro/jobs/456"/>
          <updated>2024-02-03T12:30:00Z</updated><author><name>Example Co</name></author>
          <summary>Remote US Kubernetes</summary>
        </entry></feed>'''
        jobs = self.parse_fixture("workanywhere", xml)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "Senior Platform Engineer")
        self.assertEqual(jobs[0].company, "Example Co")
        self.assertEqual(jobs[0].url, "https://workanywhere.pro/jobs/456")
        self.assertEqual(jobs[0].published, datetime(2024, 2, 3, 12, 30, tzinfo=timezone.utc))

    def test_jobicy_api_maps_us_jobs_and_salary(self):
        payload = b'''{"jobs":[{"id":789,"url":"https://jobicy.com/jobs/789",
          "jobTitle":"Senior Infrastructure Engineer","companyName":"Jobicy Co",
          "jobGeo":"United States","annualSalaryMin":190000,"annualSalaryMax":210000,
          "job_description":"Remote AWS","publication_date":"2024-01-02 03:04:05"}]}'''
        jobs = self.parse_fixture("jobicy", payload)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual((job.source, job.id, job.title, job.company),
                         ("jobicy", "789", "Senior Infrastructure Engineer", "Jobicy Co"))
        self.assertEqual((job.salary_min, job.salary_max), (190000, 210000))
        self.assertEqual(job.url, "https://jobicy.com/jobs/789")
        self.assertEqual(job.location, "United States")
        config = tomllib.loads((Path(build_feed.__file__).parent / "config.toml").read_text())
        rules = build_feed.Rules(config["filters"], config["scoring"])
        self.assertIsNone(rules.evaluate(job))

    def test_broad_regions_alone_do_not_count_as_us_eligibility(self):
        config = tomllib.loads((Path(build_feed.__file__).parent / "config.toml").read_text())
        rules = build_feed.Rules(config["filters"], config["scoring"])
        for region in ("Americas", "North America"):
            with self.subTest(region=region):
                job = build_feed.Job(
                    source="test", id=region, title="Senior Platform Engineer", company="Acme",
                    url="https://example.com/job", published=datetime.now(timezone.utc), location=region,
                )
                self.assertEqual(rules.evaluate(job), "location-unclear")

    def test_jobicy_salary_range_string_is_normalized(self):
        payload = b'''{"jobs":[{"id":790,"url":"https://jobicy.com/jobs/790",
          "jobTitle":"Senior DevOps Engineer","salary":"$160,000-$210,000"}]}'''
        job = self.parse_fixture("jobicy", payload)[0]
        self.assertEqual((job.salary_min, job.salary_max), (160000, 210000))

    def test_remotive_rss_extracts_company_from_title_and_content_encoded(self):
        xml = b'''<rss xmlns:content="http://purl.org/rss/1.0/modules/content/" version="2.0"><channel><item>
          <title>Senior DevOps Engineer at Remotive Co</title><link>https://remotive.com/remote-jobs/devops/987</link>
          <guid>987</guid><pubDate>Wed, 03 Jan 2024 10:00:00 +0000</pubDate>
          <content:encoded><![CDATA[<p>Remote US role</p>]]></content:encoded>
          <category>DevOps</category>
        </item></channel></rss>'''
        jobs = self.parse_fixture("remotive", xml)
        self.assertEqual(len(jobs), 1)
        self.assertEqual((jobs[0].title, jobs[0].company), ("Senior DevOps Engineer", "Remotive Co"))
        self.assertEqual(jobs[0].url, "https://remotive.com/remote-jobs/devops/987")
        self.assertIn("Remote US", jobs[0].description)

    def test_himalayas_atom_rss_handles_alternate_link_and_source_id(self):
        xml = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
          <id>tag:himalayas.app,2024:job-42</id><title>Staff Platform Engineer - Himalayas Inc</title>
          <link href="https://himalayas.app/jobs/42"/><published>2024-03-04T00:00:00+00:00</published>
          <content type="html">&lt;p&gt;Worldwide platform role&lt;/p&gt;</content>
          <category term="Platform"/>
        </entry></feed>'''
        jobs = self.parse_fixture("himalayas_rss", xml)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].id, "tag:himalayas.app,2024:job-42")
        self.assertEqual(jobs[0].url, "https://himalayas.app/jobs/42")
        self.assertEqual((jobs[0].title, jobs[0].company), ("Staff Platform Engineer", "Himalayas Inc"))
        self.assertIn("Worldwide", jobs[0].description)

    def test_himalayas_rss_source_uses_atom_feed(self):
        payload = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
          <id>h-rss-9</id><title>Senior Infrastructure Engineer at Himalayas Co</title>
          <link href="https://himalayas.app/jobs/9"/><published>2024-04-01T12:00:00Z</published>
          <summary>Remote US</summary></entry></feed>'''
        with patch.object(build_feed, "fetch", return_value=payload) as fetch:
            jobs = build_feed.SOURCES["himalayas_rss"]({}, "test-agent/1.0")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source, "himalayas_rss")
        self.assertEqual(jobs[0].url, "https://himalayas.app/jobs/9")
        self.assertEqual(fetch.call_args.args[0], "https://himalayas.app/jobs/rss")

    def test_himalayas_uses_existing_api_pagination(self):
        payload = json.dumps({"jobs": [{"guid": "h1", "title": "Senior Platform Engineer",
            "companyName": "Himalayas Co", "applicationLink": "https://himalayas.app/jobs/1",
            "pubDate": 1720000000, "locationRestrictions": ["United States"],
            "currency": "USD", "salaryPeriod": "annual", "minSalary": 180000, "maxSalary": 210000,
            "description": "Remote US"}], "nextCursor": None}).encode()
        with patch.object(build_feed, "fetch", return_value=payload) as fetch:
            jobs = build_feed.src_himalayas({"pages": 25}, "test-agent/1.0")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].id, "h1")
        self.assertEqual((jobs[0].salary_min, jobs[0].salary_max), (180000, 210000))
        self.assertEqual(fetch.call_args.args[0], "https://himalayas.app/jobs/api?limit=20")
        self.assertEqual(fetch.call_args.args[1], "test-agent/1.0")

    def test_invalid_entry_is_skipped_without_losing_other_entries(self):
        xml = b'''<rss><channel>
          <item><title>Senior SRE at Good Co</title><link>https://example.com/good</link><pubDate>Tue, 02 Jan 2024 03:04:05 GMT</pubDate></item>
          <item><title>Missing URL</title></item>
        </channel></rss>'''
        jobs = self.parse_fixture("remotefirstjobs", xml)
        self.assertEqual([job.url for job in jobs], ["https://example.com/good"])

    def test_invalid_feed_date_is_not_made_fresh(self):
        parsed = build_feed.from_feed_date("not a real date")
        self.assertEqual(parsed, datetime.min.replace(tzinfo=timezone.utc))

    def test_new_sources_are_enabled_and_request_the_configured_endpoints(self):
        root = Path(build_feed.__file__).parent
        config = tomllib.loads((root / "config.toml").read_text())
        test_sources = {
            "remotefirstjobs": ("https://remotefirstjobs.com/rss/jobs/devops.rss", b"<rss><channel/></rss>"),
            "workanywhere": ("https://workanywhere.pro/rss/engineer.xml", b"<feed xmlns='http://www.w3.org/2005/Atom'/>") ,
            "jobicy": ("https://jobicy.com/api/v2/remote-jobs?count=200&geo=usa&industry=engineering", b'{"jobs":[]}'),
            "remotive": ("https://remotive.com/remote-jobs/feed/devops", b"<rss><channel/></rss>"),
        }
        for source, (endpoint, payload) in test_sources.items():
            with self.subTest(source=source):
                self.assertEqual(source in {"remotefirstjobs", "jobicy"}, config["sources"][source]["enabled"])
                with patch.object(build_feed, "fetch", return_value=payload) as fetch:
                    build_feed.SOURCES[source](config["sources"][source], "test-agent/1.0")
                self.assertEqual(fetch.call_args.args[:2], (endpoint, "test-agent/1.0"))

    def test_generated_formats_preserve_canonical_url_and_source_attribution(self):
        job = build_feed.Job(
            source="remotefirstjobs", id="123", title="Senior DevOps Engineer", company="Acme",
            url="https://remotefirstjobs.com/jobs/123", published=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        feed = {"title": "Test", "link": "https://example.com/", "description": "Test feed"}
        with tempfile.TemporaryDirectory() as tmp:
            rss_path, json_path = Path(tmp) / "feed.xml", Path(tmp) / "feed.json"
            build_feed.write_rss([job], feed, rss_path)
            build_feed.write_json_feed([job], feed, json_path)
            rss_item = build_feed.ET.parse(rss_path).find("./channel/item")
            json_item = json.loads(json_path.read_text())["items"][0]
        self.assertEqual(rss_item.findtext("link"), job.url)
        self.assertEqual(rss_item.findtext("category"), job.source)
        self.assertIn('href="https://remotefirstjobs.com/rss"', rss_item.findtext("description"))
        self.assertIn('Source: remotefirstjobs', rss_item.findtext("description"))
        self.assertEqual(json_item["url"], job.url)
        self.assertEqual(json_item["_job"]["source"], job.source)
        self.assertIn('href="https://remotefirstjobs.com/rss"', json_item["content_html"])

    def test_source_failures_are_isolated_and_all_failed_build_does_not_write_feed(self):
        config = tomllib.loads((Path(build_feed.__file__).parent / "config.toml").read_text())
        job = build_feed.Job(
            source="remotefirstjobs", id="1", title="Senior Platform Engineer", company="Acme",
            url="https://example.com/jobs/1", published=datetime.now(timezone.utc),
            location="United States", description="Remote US role",
        )
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, patch.object(build_feed, "SOURCES", {
                "remotefirstjobs": lambda _cfg, _ua: [job],
                "workanywhere": lambda _cfg, _ua: (_ for _ in ()).throw(RuntimeError("source down")),
        }), patch.object(build_feed.sys, "argv", ["build_feed.py", "--config", "config.toml", "--out", tmp]), \
                patch.object(build_feed.tomllib, "loads", return_value=config):
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(build_feed.main(), 0)
            self.assertTrue((Path(tmp) / "feed.xml").is_file())

        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, patch.object(build_feed, "SOURCES", {
                "remotefirstjobs": lambda _cfg, _ua: (_ for _ in ()).throw(RuntimeError("source down")),
        }), patch.object(build_feed.sys, "argv", ["build_feed.py", "--config", "config.toml", "--out", tmp]), \
                patch.object(build_feed.tomllib, "loads", return_value=config), \
                contextlib.redirect_stdout(stdout):
            self.assertEqual(build_feed.main(), 1)
            self.assertFalse((Path(tmp) / "feed.xml").exists())


if __name__ == "__main__":
    unittest.main()
