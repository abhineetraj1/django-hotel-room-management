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
        sample_rooms = [{'number': str(i), 'category': 'Medium', 'price': 2000} for i in range(101, 111)] + \
                       [{'number': str(i), 'category': 'Large', 'price': 4000} for i in range(201, 211)]
        if sample_rooms:
            rooms_collection.insert_many(sample_rooms)

    # Ensure 'bookings' collection exists
    if 'bookings' not in db.list_collection_names():
        db.create_collection('bookings')
        print("Creating 'bookings' collection.")
    
    # Migration: Ensure existing rooms have pricing and sizing categories
    db.rooms.update_many({"price": {"$exists": False}, "category": "Standard"}, {"$set": {"price": 2000, "category": "Medium"}})
    db.rooms.update_many({"price": {"$exists": False}, "category": "Deluxe"}, {"$set": {"price": 4000, "category": "Large"}})
        
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

def is_spam_grievance(db, new_desc):
    """ AI Spam Reduction: Uses Jaccard Similarity to block highly repetitive grievances """
    recent_grievances = list(db.grievances.find().sort("datetime", -1).limit(20))
    new_words = set(new_desc.lower().split())
    if not new_words: return False
    
    for g in recent_grievances:
        g_words = set(g.get('description', '').lower().split())
        if not g_words: continue
        
        intersection = len(new_words.intersection(g_words))
        union = len(new_words.union(g_words))
        if union > 0 and (intersection / union) > 0.65:  # 65% similarity threshold
            return True
    return False

def Home(request):
	return render(request, "index.html")

def Room(request):
	if request.method == "POST":
		quantity = int(request.POST.get("quantity", 1))
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
				"check_out": {"$gt": check_in_str},
				"status": {"$ne": "checked_out"}
			}
			
			overlapping_bookings = db.bookings.find(overlap_query, {"room_number": 1})
			booked_room_numbers = {booking['room_number'] for booking in overlapping_bookings}

			# Get all available room documents
			available_rooms_cursor = db.rooms.find({"number": {"$nin": list(booked_room_numbers)}})
			
			# Group available rooms by category to fulfill the quantity request
			categories = {}
			for r in available_rooms_cursor:
				cat = r.get("category", "Medium")
				if cat not in categories:
					categories[cat] = {"count": 0, "base_price": r.get("price", 2000)}
				categories[cat]["count"] += 1

			# AI-Powered Dynamic Pricing
			demand_multiplier = predict_demand_ai(db, check_in_str)
			
			available_categories = []
			for cat_name, data in categories.items():
				# Only show categories that have enough inventory
				if data["count"] >= quantity:
					data["name"] = cat_name
					data["price"] = int(data["base_price"] * demand_multiplier)
					available_categories.append(data)
			
		finally:
			client.close()

		return render(request, "rooms.html", {
			"categories": available_categories,
			"message": "none",
			"in_d": check_in_str,
			"out_d": check_out_str,
			"quantity": quantity
		})
	else:
		return render(request, "message.html", {
			"title": "Booking Error",
			"message": "Invalid request method.",
			"url": "/",
			"button_text": "Back to Home"
		})

def Book(request, room=None, in_d=None, out_d=None, name=None, phone=None, price=None):
	if request.method == "POST":
		category = request.POST.get("category")
		quantity = int(request.POST.get("quantity", 1))
		in_d = request.POST.get("in_d")
		out_d = request.POST.get("out_d")
		name = request.POST.get("name")
		phone = request.POST.get("phone")
		price = request.POST.get("price")

	if not all([category, in_d, out_d, name, phone, price]):
		return render(request, "message.html", {
			"title": "Booking Error",
			"message": "Missing booking details. Please make sure all details are filled.",
			"url": "/",
			"button_text": "Back to Home"
		})

	# Calculate total room charge
	d1 = datetime.strptime(in_d, "%Y-%m-%d")
	d2 = datetime.strptime(out_d, "%Y-%m-%d")
	days = (d2 - d1).days
	if days <= 0: days = 1
	total_charge = days * int(price)

	db, client = get_db()
	try:
		overlap_query = {
			"check_in": {"$lt": out_d},
			"check_out": {"$gt": in_d},
			"status": {"$ne": "checked_out"}
		}
		overlapping_bookings = db.bookings.find(overlap_query, {"room_number": 1})
		booked_room_numbers = {b['room_number'] for b in overlapping_bookings}

		# Find exact number of available rooms matching the chosen category
		available_rooms = list(db.rooms.find({
			"category": category, 
			"number": {"$nin": list(booked_room_numbers)}
		}).limit(quantity))
		
		if len(available_rooms) < quantity:
			return render(request, "message.html", {
				"title": "Booking Failed",
				"message": "Apologies, but the requested quantity of rooms is no longer available.",
				"url": "/",
				"button_text": "Back to Home"
			})

		assigned_rooms = []
		for r in available_rooms:
			db.bookings.insert_one({
				"room_number": r['number'],
				"room_category": category,
				"check_in": in_d,
				"check_out": out_d,
				"guest_name": name,
				"guest_phone": phone,
				"price_per_night": int(price),
				"total_room_charge": total_charge,
				"booked_at": datetime.utcnow(),
				"expenses": [],
				"payments": [],
				"status": "booked"
			})
			assigned_rooms.append(r['number'])
	finally:
		client.close()
	return render(request, "message.html", {
		"title": "Booking Confirmed",
		"message": f"Successfully booked {quantity} room(s): {', '.join(assigned_rooms)}!",
		"url": "/list_booked",
		"button_text": "View Bookings"
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
	today_str = now.strftime("%Y-%m-%d")
	
	profit_month = 0
	profit_30 = 0
	profit_7 = 0
	total_overdue = 0

	todays_check_ins = []
	todays_check_outs = []
	unpaid_bookings = []

	try:
		bookings = list(db.bookings.find({}))
		
		# Process stats and prepare data
		rooms_dict = {}
		
		for b in bookings:
			b_id = str(b['_id'])
			b['id'] = b_id
			del b['_id']
			
			# Calculate totals
			room_charge = b.get("total_room_charge", 0)
			expenses_total = sum(e.get("amount", 0) for e in b.get("expenses", []))
			payments_total = sum(p.get("amount", 0) for p in b.get("payments", []))
			
			total_revenue = room_charge + expenses_total
			due = total_revenue - payments_total
			
			b['total_revenue'] = total_revenue
			b['total_due'] = due

			b_date = b.get("booked_at")
			if b_date:
				if b_date >= start_of_month:
					profit_month += total_revenue
				if b_date >= last_30:
					profit_30 += total_revenue
				if b_date >= last_7:
					profit_7 += total_revenue
			
			is_checked_out = b.get('status') == 'checked_out'
			
			if not is_checked_out:
				total_overdue += due
				if due > 0:
					unpaid_bookings.append(b)

			if b.get("check_in") == today_str and not is_checked_out:
				todays_check_ins.append(b)
			if b.get("check_out") == today_str and not is_checked_out:
				todays_check_outs.append(b)

			# Skip checked-out rooms for the visual list
			if is_checked_out:
				continue
			
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
		"todays_check_ins": todays_check_ins,
		"todays_check_outs": todays_check_outs,
		"unpaid_bookings": unpaid_bookings,
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
	p.setFont("Helvetica-Bold", 20)
	p.drawString(50, 750, "HOTEL INVOICE")
	
	# Details
	p.setFont("Helvetica", 12)
	y = 700
	p.drawString(50, y, f"Invoice ID: {str(booking['_id'])[-6:].upper()}")
	p.drawString(300, y, f"Date: {datetime.utcnow().strftime('%Y-%m-%d')}")
	
	y -= 30
	p.drawString(50, y, f"Guest Name: {booking.get('guest_name')}")
	p.drawString(300, y, f"Phone: {booking.get('guest_phone', 'N/A')}")
	
	y -= 20
	p.drawString(50, y, f"Room Number: {booking.get('room_number')} ({booking.get('room_category', 'Standard')})")
	
	y -= 20
	p.drawString(50, y, f"Check In: {booking.get('check_in')}")
	p.drawString(300, y, f"Check Out: {booking.get('check_out')}")
	
	# Financials
	y -= 40
	p.setFont("Helvetica-Bold", 14)
	p.drawString(50, y, "Charges Breakdown")
	
	y -= 25
	p.setFont("Helvetica", 12)
	p.drawString(50, y, "Room Charge:")
	p.drawString(400, y, f"Rs. {booking.get('total_room_charge')}")
	
	expenses = sum(e.get('amount', 0) for e in booking.get('expenses', []))
	for exp in expenses:
		y -= 20
		p.drawString(50, y, f"Extra: {exp.get('name')}")
		p.drawString(400, y, f"Rs. {exp.get('amount')}")
		
	total_charge = booking.get('total_room_charge', 0) + sum(e.get('amount', 0) for e in expenses)
	y -= 30
	p.setFont("Helvetica-Bold", 12)
	p.drawString(50, y, "Total Charges:")
	p.drawString(400, y, f"Rs. {total_charge}")
	
	y -= 40
	p.setFont("Helvetica-Bold", 14)
	p.drawString(50, y, "Payments Received")
	p.setFont("Helvetica", 12)
	
	payments = booking.get('payments', [])
	total_paid = 0
	if payments:
		for pay in payments:
			y -= 20
			pay_date = pay.get('date').strftime('%Y-%m-%d') if isinstance(pay.get('date'), datetime) else 'N/A'
			p.drawString(50, y, f"Payment on {pay_date}")
			p.drawString(400, y, f"Rs. {pay.get('amount')}")
			total_paid += pay.get('amount', 0)
	else:
		y -= 20
		p.drawString(50, y, "No payments recorded.")
		
	y -= 30
	p.setFont("Helvetica-Bold", 12)
	p.drawString(50, y, "Total Paid:")
	p.drawString(400, y, f"Rs. {total_paid}")
	
	due_amount = total_charge - total_paid
	y -= 25
	p.drawString(50, y, "Balance Due:")
	if due_amount > 0:
		p.setFillColorRGB(0.8, 0, 0)
	else:
		p.setFillColorRGB(0, 0.6, 0)
	p.drawString(400, y, f"Rs. {due_amount}")
	
	p.showPage()
	p.save()
	
	buffer.seek(0)
	return FileResponse(buffer, as_attachment=True, filename=f"invoice_{booking_id}.pdf")

def add_rooms(request):
	if request.method == "POST":
		category = request.POST.get("category")
		price = int(request.POST.get("price"))
		quantity = int(request.POST.get("quantity"))
		db, client = get_db()
		try:
			# Auto-generate room IDs by prefixing Size and incrementing count
			existing_count = db.rooms.count_documents({"category": category})
			prefix = category[0].upper()
			new_rooms = []
			for i in range(1, quantity + 1):
				new_rooms.append({
					"number": f"{prefix}-{existing_count + i}",
					"category": category,
					"price": price
				})
			if new_rooms:
				db.rooms.insert_many(new_rooms)
		finally:
			client.close()
	return HttpResponseRedirect("/list_booked")

def grievances(request):
	db, client = get_db()
	try:
		status_filter = request.GET.get("status", "all")
		query = {}
		if status_filter != "all":
			query["status"] = status_filter
			
		g_list = list(db.grievances.find(query).sort("datetime", -1))
		for g in g_list:
			g['id'] = str(g['_id'])
	finally:
		client.close()
	return render(request, "grievances.html", {"grievances": g_list, "current_filter": status_filter})

def add_grievance(request):
	if request.method == "POST":
		desc = request.POST.get("description")
		name = request.POST.get("customer_name", "Anonymous")
		status = request.POST.get("status", "pending")
		db, client = get_db()
		try:
			if is_spam_grievance(db, desc):
				return render(request, "message.html", {"title": "Spam Detected", "message": "This grievance looks too similar to a recently submitted one.", "url": "/grievances", "button_text": "Back"})
			db.grievances.insert_one({"description": desc, "customer_name": name, "datetime": datetime.utcnow(), "status": status})
		finally:
			client.close()
	return HttpResponseRedirect("/grievances")

def update_grievance(request):
	if request.method == "POST":
		g_id = request.POST.get("id")
		status = request.POST.get("status")
		db, client = get_db()
		try:
			db.grievances.update_one({"_id": ObjectId(g_id)}, {"$set": {"status": status}})
		finally:
			client.close()
	return HttpResponseRedirect("/grievances")
