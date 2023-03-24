import shutil
import os
from django.http import HttpResponse
from django.shortcuts import render
from datetime import *

def Home(request):
	return render(request, "index.html")

def Room(request):
	if (request.method == "POST"):
		dt = list_days(request.POST.get("check_in"), request.POST.get("check_out"))
		print(dt)
		r=[]
		for i in os.listdir("rooms"):
			st=open("rooms/"+i+"/status.txt","r").read()
			if (st == "clear"):
				r.append(str(i))
			else:
				de = []
				h= open("rooms/"+i+"/date.txt","r").read().split("\n")
				for k in dt:
					if (k not in h):
						de.append(True)
					else:
						de.append(False)
				if (False not in de):
					r.append(str(i))
		return render(request, "rooms.html",{"r":r,"message":"none","in_d":request.POST.get("check_in"),"out_d":request.POST.get("check_out")})
	else:
		return HttpResponse("<a href='/'><button>back</button></a>")

def Book(request,room, in_d, out_d, details):
	r, tr=[],""
	for i in list_days(in_d,out_d):
		tr = tr+i +"\n"
	open("rooms/"+room+"/date.txt","a").write(tr)
	open("rooms/"+room+"/status.txt","w").write("booked")
	open("rooms/"+room+"/detail.txt","a").write(details+" "+in_d+","+out_d+"\n")
	return HttpResponse("Booked")

def list_days(a,b):
	o = []
	d = date(int(a.split("-")[0]), int(a.split("-")[1]), int(a.split("-")[2])) - date(int(b.split("-")[0]), int(b.split("-")[1]), int(b.split("-")[2]))
	d=d.days-(2* d.days)
	n = date(int(a.split("-")[0]), int(a.split("-")[1]), int(a.split("-")[2]))
	for i in range(0, 22):
		n = n+ timedelta(days=i)
		o.append(str(n))
	return o

def list_booked(request):
	g=[]
	for i in os.listdir("rooms"):
		if (open("rooms/"+i+"/status.txt","r").read() == "booked"):
			g.append({"number":i,"dates":open("rooms/"+i+"/date.txt","r").read().replace("\n","<br>")})
	return render(request, "pr.html", {"room":g})

def deBook(request, room):
	shutil.rmtree("rooms/"+room)
	os.mkdir("rooms/"+room)
	open("rooms/"+room+"/status.txt","a").write("clear")
	open("rooms/"+room+"/detail.txt","a").write("")
	open("rooms/"+room+"/date.txt","a").write("")
	return HttpResponse("Debooked")
