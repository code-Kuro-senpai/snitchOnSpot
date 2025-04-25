import os
import json
from datetime import datetime, timezone
from flask import Flask, render_template, request, redirect, url_for, flash
# --- Add SQLAlchemy Imports ---
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func # For default timestamp if needed

# --- Add DotEnv for Local Dev ---
from dotenv import load_dotenv

load_dotenv() # Load environment variables from .env file

# --- Add Geopy Imports ---
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import time

app = Flask(__name__)

# --- Configure SQLAlchemy ---
# Get DATABASE_URL from environment variable (set by Vercel or .env)
database_url = os.environ.get('POSTGRES_URL')
if not database_url:
    raise RuntimeError("POSTGRES_URL environment variable not set.")

# Ensure the scheme is postgresql:// for SQLAlchemy < 2.0 compatibility
# SQLAlchemy 2.0+ handles postgres:// better
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False # Disable modification tracking (saves resources)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default-fallback-secret-key-CHANGE-ME') # Get secret key

db = SQLAlchemy(app) # Initialize SQLAlchemy
# --- End SQLAlchemy Config ---


# --- Define Report Categories (keep this) ---
REPORT_CATEGORIES = [
    "Theft", "Vandalism", "Noise Complaint", "Suspicious Activity",
    "Assault", "Traffic Issue", "Other"
]

# --- Initialize Geocoder (keep this) ---
# UPDATE your user_agent to be specific and identifiable
geolocator = Nominatim(user_agent="snitch_on_spot_flask_app/1.0")
# --- End Geocoder Init ---

print("--- Flask App Initialized with DB Config ---")


# --- Define Database Model ---
class Report(db.Model):
    __tablename__ = 'reports' # Optional: Define table name explicitly

    id = db.Column(db.Integer, primary_key=True)
    location_text = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(100), nullable=False)
    # Let the database handle the default timestamp generation in UTC
    timestamp = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)

    def __repr__(self): # Optional: For debugging representation
        return f'<Report {self.id}: {self.category} at {self.location_text[:20]}>'
# --- End Database Model ---


# --- Geocoding Function (keep this) ---
def geocode_location(location_str):
    """Attempts to geocode a location string to (lat, lon). Returns (None, None) on failure."""
    if not location_str: # Handle empty input
        return None, None
    try:
        print(f"--- Geocoding attempt for: '{location_str}' ---")
        location = geolocator.geocode(location_str, timeout=10)
        # Add a small delay to respect Nominatim's usage policy (1 req/sec)
        time.sleep(1.1) # Be mindful of this delay affecting user experience
        if location:
            print(f"--- Geocoding success: ({location.latitude}, {location.longitude}) ---")
            return location.latitude, location.longitude
        else:
            print(f"--- Geocoding failed: No location found for '{location_str}' ---")
            return None, None
    except GeocoderTimedOut:
        print("--- Geocoding failed: Service timed out ---")
        return None, None
    except GeocoderServiceError as e:
        print(f"--- Geocoding failed: Service error - {e} ---")
        return None, None
    except Exception as e: # Catch any other unexpected errors during geocoding
        print(f"--- Geocoding failed: Unexpected error - {e} ---")
        return None, None
# --- End Geocoding Function ---


# --- No longer need load_reports() or save_reports() for JSON files ---


@app.route('/')
def index():
    """Displays the crime reporting form."""
    print("--- Entered / route (index) ---")
    return render_template('index.html', categories=REPORT_CATEGORIES)


@app.route('/report', methods=['POST'])
def report():
    """Handles submission of a new crime report."""
    print("--- Entered /report route (POST request) ---")
    location_text = request.form.get('location')
    description = request.form.get('description')
    category = request.form.get('category')
    print(f"--- Received RAW | Location Text: {location_text}, Desc: {description}, Cat: {category} ---")

    # --- Validation ---
    if not location_text or not description or not category:
        flash("Location, description, and category are required.", "error")
        print("--- Validation failed: Missing required field(s) ---")
        return redirect(url_for('index'))
    if category not in REPORT_CATEGORIES:
         flash("Invalid category selected.", "error")
         print("--- Validation failed: Invalid category ---")
         return redirect(url_for('index'))
    # --- End validation ---

    # === Sanitize User Input Strings ===
    # Replace newline characters and tabs with spaces before saving to DB
    sanitized_location_text = location_text.replace('\r\n', ' ').replace('\n', ' ').replace('\t', ' ')
    sanitized_description = description.replace('\r\n', ' ').replace('\n', ' ').replace('\t', ' ')
    print(f"--- SANITIZED | Location Text: {sanitized_location_text}, Desc: {sanitized_description} ---")
    # === End Sanitization ===

    # --- Attempt Geocoding (using original text for better accuracy) ---
    latitude, longitude = geocode_location(location_text)
    # --- End Geocoding Attempt ---

    # --- Create and Save Report to Database ---
    try:
        new_db_report = Report(
            location_text=sanitized_location_text, # Save sanitized version
            description=sanitized_description,   # Save sanitized version
            category=category,
            # Timestamp is handled by server_default=func.now() in the model
            latitude=latitude,
            longitude=longitude
        )
        db.session.add(new_db_report) # Add the new report object to the session
        db.session.commit()           # Commit the transaction to save to DB
        print(f"--- Report saved to DB with ID: {new_db_report.id} ---")

        # --- Flash message based on geocoding result ---
        if latitude is None and longitude is None:
            flash("Report submitted, but the location could not be precisely found on the map.", "warning")
        else:
            flash("Report submitted successfully!", "success")
        # --- End Flash message ---

    except Exception as e:
        db.session.rollback() # Roll back the transaction on error
        print(f"--- ERROR saving report to DB: {e} ---")
        # Consider more specific error logging here in a real app
        flash("An error occurred while submitting the report. Please try again.", "error")
        return redirect(url_for('index')) # Redirect back to form on error
    # --- End Database Save ---

    return redirect(url_for('view_reports'))


@app.route('/view_reports')
def view_reports():
    """Displays all submitted crime reports and the map."""
    print("--- Entered /view_reports route ---")
    try:
        # --- Query reports from Database, ordered by timestamp descending ---
        all_sorted_reports = Report.query.order_by(Report.timestamp.desc()).all()
        # --- End Query ---

        # --- Prepare data for the map (list of dicts) ---
        map_reports_data = []
        for report in all_sorted_reports:
            # Only include reports that have valid coordinates
            if report.latitude is not None and report.longitude is not None:
                map_reports_data.append({
                    'latitude': report.latitude,
                    'longitude': report.longitude,
                    'category': report.category,
                    'location_text': report.location_text,
                    'description': report.description,
                    'timestamp': report.timestamp.isoformat() # Convert datetime to ISO string for JSON
                })
        # --- End Prepare Map Data ---

        print(f"--- Loaded {len(all_sorted_reports)} total reports for viewing. Found {len(map_reports_data)} with coordinates for map. ---")

        return render_template(
            'view_reports.html',
            reports=all_sorted_reports,   # Pass list of Report objects for the list view
            map_reports=map_reports_data  # Pass list of DICTS for the map script
        )

    except Exception as e:
        print(f"--- ERROR loading reports from DB: {e} ---")
        flash("An error occurred while loading reports.", "error")
        # Render template with empty lists or redirect, depending on desired UX
        return render_template('view_reports.html', reports=[], map_reports=[])


# Function to create tables (run manually via flask shell)
def init_db():
     with app.app_context():
        print("--- Creating database tables (if they don't exist)... ---")
        db.create_all()
        print("--- Database tables checked/created. ---")

# Main execution block
if __name__ == '__main__':
    # DO NOT CALL init_db() here in production or regular runs.
    # Run `flask shell` then `from app import init_db` then `init_db()` manually.
    print("--- Starting Flask development server ---")
    # Change host/port as needed for local dev access
    app.run(debug=True, host='0.0.0.0', port=5000)