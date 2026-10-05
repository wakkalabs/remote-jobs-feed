# remote-jobs-feed

A scheduled GitHub Action that pulls remote job boards, keeps only **fully remote, US-eligible, senior platform/infrastructure roles**, scores them, and publishes:

- `feed.xml` — RSS 2.0 for any reader
- `feed.json` — JSON Feed 1.1 with a `_job` block (company, location, salary, score, source) for agents like Hermes job-radar

## Sources

| Source | Endpoint |
|---|---|
| We Work Remotely | `weworkremotely.com/categories/<category>.rss` |
| Remote OK | `remoteok.com/api` |
| Himalayas API | `himalayas.app/jobs/api` (cursor-paginated; 20 per page) |
| Himalayas RSS/Atom (optional; limited recent entries) | `himalayas.app/jobs/rss` (most recent jobs) |
| RemoteFirstJobs | `remotefirstjobs.com/rss/jobs/devops.rss` (RSS) |
| WorkAnywhere | `workanywhere.pro/rss/engineer.xml` (RSS/Atom) |
| Jobicy | `jobicy.com/api/v2/remote-jobs?count=200&geo=usa&industry=engineering` (JSON API) |
| Remotive | `remotive.com/remote-jobs/feed/devops` (RSS) |
| HN "Who is hiring?" | `hn.algolia.com/api/v1` (latest monthly thread) |

RSS/Atom sources preserve canonical listing links, normalize feed dates and descriptions, and record source attribution. Each source can be independently disabled with its `[sources.<name>].enabled` flag in `config.toml`. The RemoteFirstJobs and Jobicy providers require attribution; the generated items link to original postings and identify those sources. Check each provider's current terms before redistributing this feed.

Each source fails independently. The build only aborts if every source fails, so a bad run never publishes an empty feed.

## Setup

1. Create a **public** repo (e.g. `remote-jobs-feed`) and push these files. Pages on private repos needs a paid plan.
2. Repo **Settings → Pages → Source: GitHub Actions**.
3. Run the workflow once from the **Actions** tab (or push to `main`).
4. Set `feed.link` in `config.toml` to your Pages URL, e.g. `https://<user>.github.io/remote-jobs-feed/`.
5. Subscribe to `<pages-url>/feed.xml`, and point job-radar at `<pages-url>/feed.json`.

The workflow runs every 4 hours at :17.

## Tuning

Everything lives in `config.toml`:

- `title_include` / `title_exclude` / `seniority` set which titles count.
- `reject_phrases` drops hybrid, onsite, relocation-required and clearance roles when they appear anywhere in the location or description.
- `us_ok_*`, `non_us_location` and `anywhere_location` handle US eligibility. A non-US location is rejected unless a US signal is also present.
- `min_salary_usd` drops roles whose posted max is below the floor. Unknown salaries are kept.
- `[scoring]` keyword hits add points, staff/principal/architect titles get a bonus, and salaries at or above target get a bonus. The score shows as `[N]` in each RSS title.

To see why each job was kept or dropped:

```sh
python build_feed.py --explain
```

Needs Python 3.11+. No dependencies.
