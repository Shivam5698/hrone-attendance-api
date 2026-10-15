# DECISIONS.md

Architecture and Design Notes
#-----------------------------------#

Here is a quick breakdown of why I built things the way I did, especially around database performance, concurrency, and handling edge cases.

#-------------------------------------#

Database Indexing Strategy
I set up a few specific indexes to keep queries fast and enforce data integrity. For the `attendance_logs` collection, I added a unique compound index on `(emp_code, date)`. This guarantees we only store one record per employee per day and makes looking up specific employees very fast. To support the lists and leaderboards with the correct sorting, I went with `(date desc, emp_code)`. I also added `(status, date desc, emp_code)` specifically to optimize queries using the status filter.

#-------------------------------------#

For the `employees` collection, it is fairly standard: a unique index on `emp_code`, plus `(department, emp_code)` for department filtering, and one on `joined_on` to help match headcounts. I actually considered adding an `(emp_code, punch_in)` index specifically to speed up punch-outs, but I ultimately rejected it. A single employee won't have that many records in the grand scheme of things, so just relying on the `emp_code` prefix from our unique index is plenty fast.

#-------------------------------------#

Handling Concurrency (The Punch-In Race)
Instead of doing a "find-then-insert" (which usually causes race conditions if two requests hit at the exact same time), I decided to let the database do the heavy lifting. Both incoming requests go straight to `insert_one`. Because of the unique `(emp_code, date)` index mentioned earlier, MongoDB will naturally accept the first insert and throw a `DuplicateKeyError` for the second one. I just catch that database error and return a clean 409 Conflict status to the client.

#-------------------------------------#

Leaderboard Ties
When generating the leaderboard, I used `$rank`. This means if two employees are tied, they get the exact same rank and the system skips the next number (e.g., ranks go 1, 1, 3). I made sure to apply the `limit` only after the ranking is calculated (`rank <= limit`). Because of this, if there is a tie right at the cutoff point, everyone involved in the tie still gets returned in the response, neatly ordered by their employee code.

#--------------------------------------#

Headcount Logic
When calculating department headcounts, I intentionally started the aggregation pipeline from the `employees` collection rather than the attendance logs. By using `$lookup` with `preserveNullAndEmptyArrays`, we keep employees in the result set even if they have zero attendance logs for that period. The final headcount is simply calculated by taking the size of the `$addToSet` of employee codes.

#--------------------------------------#

Future Scaling (100x Data Volume)
Right now, aggregating raw logs on the fly works fine. But if this system needs to scale to handle 100x the current data, that approach will get too slow. If we reach that point, I would move to pre-computing the data. Every time a punch-out or manual time correction is saved, we would update that employee's monthly totals in a separate summary collection. Then, the analytics endpoints would just read from that small, pre-calculated table instead of crunching millions of raw logs every single time someone opens the dashboard.