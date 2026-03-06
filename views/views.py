from django.http import HttpResponse, HttpResponseRedirect, FileResponse
from django.shortcuts import render
from datetime import datetime, timedelta
from pymongo import MongoClient
from bson.objectid import ObjectId
import io

try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

def get_db():
    """
    Connects to MongoDB, ensures collections exist, and returns the db object and client.
    The client must be closed by the caller.
    """
    client = MongoClient('mongodb://localhost:27017/')
    db = client['hotel_db']
    
    # Ensure 'rooms' collection exists and has data
    if 'rooms' not in db.list_collection_names():
        print("Creating 'rooms' collection and populating with sample rooms.")
        rooms_collection = db['rooms']
        sample_rooms = [{'number': str(i), 'category': 'Standard'} for i in range(101, 111)] + \
                       [{'number': str(i), 'category': 'Deluxe'} for i in range(201, 211)]
        if sample_rooms:
            rooms_collection.insert_many(sample_rooms)

    # Ensure 'bookings' collection exists
    if 'bookings' not in db.list_collection_names():
        db.create_collection('bookings')
        print("Creating 'bookings' collection.")
    
    # Migration: Ensure existing rooms have categories if they were created previously
    db.rooms.update_many({"category": {"$exists": False}, "number": {"$regex": "^1"}}, {"$set": {"category": "Standard"}})
    db.rooms.update_many({"category": {"$exists": False}, "number": {"$regex": "^2"}}, {"$set": {"category": "Deluxe"}})
        
    return db, client

def predict_demand_ai(db, check_in_date_str):
    """
    AI/ML Feature: Predicts demand multiplier based on historical booking data.
    Uses a simple frequency-based probability model.
    """
    try:
        check_in_date = datetime.strptime(check_in_date_str, "%Y-%m-%d")
        month = check_in_date.month
        
        # Fetch historical data for training/prediction
        bookings = list(db.bookings.find({}, {"check_in": 1}))
        if not bookings:
            return 1.0
            
        # Feature Extraction: Count bookings in the same month
        month_count = 0
        total_count = len(bookings)
        
        for b in bookings:
            try:
                b_date = datetime.strptime(b['check_in'], "%Y-%m-%d")
                if b_date.month == month:
                    month_count += 1
            except:
                continue
        
        # AI Logic: If probability of booking in this month is high (> 20%), increase price
        prob = month_count / total_count
        if total_count > 5 and prob > 0.2:
            return 1.0 + (prob * 0.5) # Dynamic increase based on demand probability
        
        return 1.0
    except Exception as e:
        print(f"AI Prediction Error: {e}")
        return 1.0

def Home(request):
	return render(request, "index.html")

def Room(request):
	if request.method == "POST":
		check_in_str = request.POST.get("check_in")
		check_out_str = request.POST.get("check_out")

		if not check_in_str or not check_out_str or check_in_str >= check_out_str:
			return render(request, "message.html", {
				"title": "Invalid Dates",
				"message": "Check-out must be after check-in. Please go back and try again.",
				"url": "/",
				"button_text": "Back to Search"
			})

		db, client = get_db()
		try:
			# Find rooms that have bookings overlapping with the requested date range.
			# A booking overlaps if (booking.check_in < requested_checkout) AND (booking.check_out > requested_checkin).
			overlap_query = {
				"check_in": {"$lt": check_out_str},
				"check_out": {"$gt": check_in_str}
			}
			
			overlapping_bookings = db.bookings.find(overlap_query, {"room_number": 1})
			booked_room_numbers = {booking['room_number'] for booking in overlapping_bookings}

			# Get all room numbers from the rooms collection
			all_rooms = db.rooms.find({}, {"number": 1, "_id": 0})
			all_room_numbers = {room['number'] for room in all_rooms}

			# Available rooms are the set difference between all rooms and booked rooms
			available_room_numbers = sorted(list(all_room_numbers - booked_room_numbers))
			
			# AI-Powered Dynamic Pricing
			base_price = 2000
			demand_multiplier = predict_demand_ai(db, check_in_str)
			
			# Apply AI multiplier
			price = int(base_price * demand_multiplier)
			
		finally:
			client.close()

		return render(request, "rooms.html", {
			"r": available_room_numbers,
			"message": "none",
			"in_d": check_in_str,
			"out_d": check_out_str,
			"price": int(price)
		})
	else:
		return render(request, "message.html", {
			"title": "Welcome",
			"message": "Please use the home page to search for rooms.",
			"url": "/",
			"button_text": "Go Home"
		})

def Book(request, room, in_d, out_d, name, phone, price):
	# Calculate total room charge
	d1 = datetime.strptime(in_d, "%Y-%m-%d")
	d2 = datetime.strptime(out_d, "%Y-%m-%d")
	days = (d2 - d1).days
	total_charge = days * int(price)

	db, client = get_db()
	try:
		# Fetch room category
		room_doc = db.rooms.find_one({"number": room})
		category = room_doc.get("category", "Standard") if room_doc else "Standard"

		booking_doc = {
			"room_number": room,
			"room_category": category,
			"check_in": in_d,
			"check_out": out_d,
			"guest_name": name,
			"guest_phone": phone,
			"price_per_night": int(price),
			"total_room_charge": total_charge,
			"booked_at": datetime.utcnow(),
			"expenses": [],
			"payments": []
		}
		db.bookings.insert_one(booking_doc)
	finally:
		client.close()
	return render(request, "message.html", {
		"title": "Booking Confirmed",
		"message": f"Room {room} has been booked successfully!",
		"url": "/",
		"button_text": "Back to Home"
	})

def add_expense(request):
	if request.method == "POST":
		booking_id = request.POST.get("booking_id")
		name = request.POST.get("expense_name")
		amount = int(request.POST.get("expense_amount"))
		
		db, client = get_db()
		try:
			db.bookings.update_one(
				{"_id": ObjectId(booking_id)},
				{"$push": {"expenses": {
					"name": name,
					"amount": amount,
					"date": datetime.utcnow()
				}}}
			)
		finally:
			client.close()
		return HttpResponseRedirect("/list_booked")

def add_payment(request):
	if request.method == "POST":
		booking_id = request.POST.get("booking_id")
		amount = int(request.POST.get("payment_amount"))
		
		db, client = get_db()
		try:
			db.bookings.update_one(
				{"_id": ObjectId(booking_id)},
				{"$push": {"payments": {
					"amount": amount,
					"date": datetime.utcnow()
				}}}
			)
		finally:
			client.close()
		return HttpResponseRedirect("/list_booked")

def list_booked(request):
	db, client = get_db()
	booked_rooms_info = []
	
	# Stats
	now = datetime.utcnow()
	start_of_month = datetime(now.year, now.month, 1)
	last_30 = now - timedelta(days=30)
	last_7 = now - timedelta(days=7)
	
	profit_month = 0
	profit_30 = 0
	profit_7 = 0
	total_overdue = 0

	try:
		bookings = list(db.bookings.find({}))
		
		# Process stats and prepare data
		rooms_dict = {}
		
		for b in bookings:
			# Skip checked-out rooms for the visual list, but keep them for stats above
			if b.get('status') == 'checked_out':
				continue

			# Calculate totals
			room_charge = b.get("total_room_charge", 0)
			expenses_total = sum(e.get("amount", 0) for e in b.get("expenses", []))
			payments_total = sum(p.get("amount", 0) for p in b.get("payments", []))
			
			total_revenue = room_charge + expenses_total
			due = total_revenue - payments_total
			total_overdue += due
			
			b_date = b.get("booked_at")
			if b_date:
				if b_date >= start_of_month:
					profit_month += total_revenue
				if b_date >= last_30:
					profit_30 += total_revenue
				if b_date >= last_7:
					profit_7 += total_revenue
			
			b['id'] = str(b['_id'])
			del b['_id']
			b['total_revenue'] = total_revenue
			b['total_due'] = due
			
			r_num = b['room_number']
			if r_num not in rooms_dict:
				rooms_dict[r_num] = []
			rooms_dict[r_num].append(b)

		for r_num in sorted(rooms_dict.keys()):
			room_bookings = sorted(rooms_dict[r_num], key=lambda x: x['check_in'])
			booked_rooms_info.append({
				"number": r_num,
				"bookings": room_bookings
			})

	finally:
		client.close()
		
	return render(request, "pr.html", {
		"room": booked_rooms_info,
		"stats": {
			"month": profit_month,
			"last_30": profit_30,
			"last_7": profit_7,
			"overdue": total_overdue
		}
	})

def deBook(request, room):
	db, client = get_db()
	try:
		# Soft delete: Mark as checked_out instead of deleting data
		result = db.bookings.update_many(
			{"room_number": room, "status": {"$ne": "checked_out"}},
			{"$set": {"status": "checked_out"}}
		)
		message = f"Room {room} has been checked out. {result.modified_count} booking(s) archived."
	finally:
		client.close()
	
	return render(request, "message.html", {
		"title": "Check Out Successful",
		"message": message,
		"url": "/list_booked",
		"button_text": "Back to List"
	})

def download_invoice(request, booking_id):
	if not HAS_REPORTLAB:
		return HttpResponse("PDF generation requires 'reportlab'. Please install it: pip install reportlab")

	db, client = get_db()
	try:
		booking = db.bookings.find_one({"_id": ObjectId(booking_id)})
	finally:
		client.close()

	if not booking:
		return HttpResponse("Booking not found")

	buffer = io.BytesIO()
	p = canvas.Canvas(buffer, pagesize=letter)
	
	# Header
	p.setFont("Helvetica-Bold", 16)
	p.drawString(100, 750, "HOTEL INVOICE")
	
	# Details
	p.setFont("Helvetica", 12)
	y = 700
	p.drawString(100, y, f"Guest Name: {booking.get('guest_name')}")
	p.drawString(100, y-20, f"Room Number: {booking.get('room_number')} ({booking.get('room_category', 'Standard')})")
	p.drawString(100, y-40, f"Check In: {booking.get('check_in')}")
	p.drawString(100, y-60, f"Check Out: {booking.get('check_out')}")
	
	# Financials
	p.drawString(100, y-100, f"Room Charge: ${booking.get('total_room_charge')}")
	expenses = sum(e.get('amount', 0) for e in booking.get('expenses', []))
	p.drawString(100, y-120, f"Extra Expenses: ${expenses}")
	p.drawString(100, y-140, f"Total Total: ${booking.get('total_room_charge') + expenses}")
	
	p.showPage()
	p.save()
	
	buffer.seek(0)
	return FileResponse(buffer, as_attachment=True, filename=f"invoice_{booking_id}.pdf")
