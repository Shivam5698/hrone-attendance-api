# REVIEW.md

Defects found in the starter `app/main.py`. Each one was reproduced with a concrete input before it was fixed.

=-----------------------------------=
Bug Fixes & Observations
=-----------------------------------=

I spent some time going through the code and found quite a few logic bugs and edge cases. Here is a breakdown of what I fixed:

**Time Calculation Issues**
The helper functions for calculating time had a few blind spots. In `compute_late_minutes`, the 10-minute grace period was flawed because it floored the seconds before checking. So, if someone was 10 mins and 1 second late, it counted as exactly 10 and didn't mark them late (violating requirement R2). I fixed this by checking the raw seconds against 600 first. Night shifts were also breaking this function because it used the punch-in date to calculate the shift start. I fixed it by passing the actual attendance date (`date_str`).

I also noticed `compute_work_hours` was using standard Python rounding (half to even), but the specs needed half-up rounding. For example, 0.125 was rounding to 0.12 instead of 0.13. I swapped it to use `Decimal` with `ROUND_HALF_UP`.

As for `compute_overtime`, it completely missed the 30-minute minimum rule, so leaving 10 minutes late gave you 10 minutes of OT. I added a quick `>= 30` check. Similar to the late minutes issue, night shifts were breaking here too because it calculated the shift end on the record's date, putting it a day early. I patched it to check if `shift_end <= shift_start` so it knows to push the end time to the next day.

**Employee Endpoints**
The `GET /employees` route had some bad pagination logic. The formula `skip = page * page_size` meant asking for page 1 actually skipped the first page entirely. I changed it to `(page - 1)`. Also, the `total` count at the bottom of the response was ignoring department filters, so I updated the count query to match the find query. Pagination in general was unstable because it accepted wild values (like page 0 or size 1000) and had no sorting. I added FastAPI `Query` limits and a default sort by `emp_code`.

The `POST /employees` route was basically the wild west. It had no validation, meaning you could submit a start date like "2026-13-45". Worse, it used a "check-then-insert" method for duplicates, meaning two concurrent requests could both succeed. I fixed this by adding strict Pydantic validators, relying on a unique MongoDB index for `emp_code` (catching the 409 error), and making sure `created_at` used IST-aware datetimes.

**Attendance Endpoints & Database**
For `POST /attendance/punch-in`, sending a fake employee code crashed the server with a 500 error instead of a clean 404, which is now fixed. It was also accepting random strings for the status (like "BANANA") and taking timestamps in seconds. I locked down the status field with `Literal` types and enforced millisecond validation. Timestamps were also relying on the server's local timezone and keeping microseconds, which messed up dates if the server ran in UTC. I forced everything to IST and stripped the microseconds. 

There was another race condition here allowing duplicate punch-ins if sent at the exact same time. I fixed it by creating a unique compound index on `(emp_code, date)`. 

The `GET /attendance` route was a ticking time bomb for memory. It loaded every matching document into memory before sorting and slicing in Python. I optimized this by pushing all filtering, sorting, skipping, and limiting directly into the MongoDB query and added indexes to support it.

Lastly, our `MongoClient` wasn't timezone aware. Pulling naive dates from Mongo and subtracting them from aware dates was causing random 500 errors. Adding `tz_aware=True` to the client fixed it. I also created a `record_out` helper because endpoints were leaking MongoDB `_id`s and raw datetimes instead of the required epoch milliseconds.

=-----------------------------------=
Notes & Things Kept As-Is
=-----------------------------------=

* I noticed that `GET /employees?department=...` acts as an exact, case-sensitive match. Since the OpenAPI contract didn't explicitly ask for case-insensitivity, I decided to leave it exactly as it is.
* The three original time calculation helper functions actually proved to be pretty reusable once their logic bugs were patched up. Instead of rewriting them from scratch, I just kept them in place.