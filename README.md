Employee Attendance and Analytics API
=-----------------------------------=

This is a backend service for tracking employee attendance and generating related analytics. It relies on FastAPI and MongoDB. To keep the project structure simple, all the application code is housed within a single file: `app/main.py`.


Setup Instructions
=-----------------------------------=

Before starting, ensure your system has the following installed:
- Python 3.11 or a newer version.
- MongoDB 6.0 or higher (a local instance or a MongoDB Atlas cluster will both work).

Follow these steps to get the project running on your local machine:

1. Create a virtual environment and activate it:
   ```bash
   python -m venv .venv
   
   # For Windows:
   .venv\Scripts\activate
   
   # For macOS / Linux:
   source .venv/bin/activate
   ```

2. Install the necessary packages:
   ```bash
   pip install -r requirements.txt
   ```

3. Set up your environment variables. You will need to create a `.env` file in the root directory, right next to the `app/` folder. This file is not tracked by git for security reasons. Add your specific database details like this:
   ```env
   MONGO_URI=mongodb+srv://<user>:<password>@<cluster>/?retryWrites=true&w=majority
   MONGO_DB=<database name>
   ```

4. Start the application:
   ```bash
   uvicorn app.main:app --port 8000
   ```

Note: Database indexes are created automatically when the application starts. You can test all the endpoints via the interactive Swagger documentation at `http://127.0.0.1:8000/docs`.


Available Endpoints
=-----------------------------------=

The following API routes are fully implemented and working:

System & Employees:
- `GET /health` : A simple check to see if the server is running.
- `POST /employees` and `GET /employees` : Routes for adding and listing employee records.

Attendance Tracking:
- `POST /attendance/punch-in` : Records an employee's punch-in time. This is protected against race conditions using a unique database index.
- `POST /attendance/punch-out` : Records punch-outs and is designed to correctly handle shifts that cross over midnight.
- `GET /attendance` : Fetches standard attendance records.
- `PATCH /attendance/{emp_code}/{date}` : Allows for manual time adjustments. It relies on optimistic concurrency checks to prevent accidental overwrites and keeps a detailed audit trail in the history logs.

Analytics & Admin Tools:
- `GET /analytics/employees/{emp_code}/monthly` : Retrieves monthly attendance stats for a specific user.
- `GET /analytics/departments/summary` : Provides a high-level overview of department metrics.
- `GET /analytics/leaderboard/late` : Lists employees who are frequently late.
- `GET /admin/explain/{endpoint}` : Returns the underlying MongoDB execution plans for performance debugging. Currently supports attendance_list, employee_monthly, department_summary, and late_leaderboard.


Pending Features
=-----------------------------------=

The following items are missing or not yet fully developed:
- `GET /analytics/departments/{department}/trend`
- `GET /admin/explain/department_trend` (This will currently return a 501 Not Implemented response)


Things to Keep in Mind
=-----------------------------------=

There are a couple of known limitations you should be aware of while using the API:

- The `avg_work_hours` value is calculated by MongoDB using double precision and then rounded half-up. Because of standard floating-point arithmetic, if a calculated average lands exactly on a `.xx5` boundary, it might occasionally round in the opposite direction than expected.
- When filtering the employee list, the department query (`GET /employees?department=`) is strictly case-sensitive.