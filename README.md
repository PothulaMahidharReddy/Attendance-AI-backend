# Biometric Attendance Backend

This is a FastAPI-based backend for the Biometric Attendance system. It interacts with MongoDB for data storage and Groq API for AI-powered data analysis.

## Project Structure

- `main.py`: Entry point of the FastAPI application. Contains API endpoints.
- `models.py`: Pydantic models for data validation and serialization.
- `database.py`: MongoDB connection setup using Motor (async driver).
- `.env`: Environment variables (API keys, DB URI).
- `requirements.txt`: Python dependencies.

## Setup Instructions

1.  **Create and Activate Virtual Environment:**
    ```powershell
    # Navigate to backend directory if you're not there
    cd backend

    # Create virtual environment
    python -m venv venv

    # Activate virtual environment (Windows)
    .\venv\Scripts\activate
    ```

2.  **Install Python Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

2.  **Configure Environment Variables:**
    Open the `.env` file and update the following values:
    - `MONGO_URI`: Your MongoDB connection string.
    - `DB_NAME`: The name of your database (default: `attendance_db`).
    - `COLLECTION_NAME`: The name of your collection (default: `biometricdatas`).
    - `GROQ_API_KEY`: Your Groq API key for AI analysis.

3.  **Run the Backend:**
    ```bash
    python -m backend.main
    ```
    Or using uvicorn directly:
    ```bash
    uvicorn backend.main:app --reload
    ```

## API Endpoints

- `GET /attendance`: List all attendance records.
- `GET /attendance/{id}`: Get a specific record by ID.
- `POST /attendance`: Create a new record.
- `PUT /attendance/{id}`: Update an existing record.
- `DELETE /attendance/{id}`: Delete a record.
- `POST /analyze`: Get AI-powered insights from recent attendance data.
- `GET /`: Health check endpoint.
