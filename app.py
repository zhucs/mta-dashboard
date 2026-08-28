import json
import os
import time

import requests
from flask import Flask, jsonify, request, send_from_directory
from google.transit import gtfs_realtime_pb2

app = Flask(__name__, static_folder="static")

FEED_URLS = {
    "1": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs",
    "ace": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-ace",
    "l": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-l",
    "nqrw": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-nqrw",
    "jz": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-jz",
    "g": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-g",
    "bdfm": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-bdfm",
    "si": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-si",
}

ROUTE_TO_FEED = {
    "1": "1", "2": "1", "3": "1", "4": "1", "5": "1", "6": "1", "7": "1", "GS": "1",
    "A": "ace", "C": "ace", "E": "ace",
    "L": "l",
    "N": "nqrw", "Q": "nqrw", "R": "nqrw", "W": "nqrw",
    "J": "jz", "Z": "jz",
    "G": "g",
    "B": "bdfm", "D": "bdfm", "F": "bdfm", "M": "bdfm",
    "SI": "si",
}

with open(os.path.join(os.path.dirname(__file__), "route_colors.json")) as f:
    ROUTE_COLORS = json.load(f)

with open(os.path.join(os.path.dirname(__file__), "stations_index.json")) as f:
    STATION_INDEX = {s["stop_id"]: s for s in json.load(f)}

STATIONS = [
    {
        "stop_id": "117",
        "name": "116 St-Columbia University",
        "route": "1",
        "feed": "1",
        "platforms": {"N": "117N", "S": "117S"},
        "labels": {"N": "Uptown", "S": "Downtown"},
    },
    {
        "stop_id": "125",
        "name": "59 St-Columbus Circle",
        "route": "1",
        "feed": "1",
        "platforms": {"N": "125N", "S": "125S"},
        "labels": {"N": "Uptown", "S": "Downtown"},
    },
    {
        "stop_id": "L11",
        "name": "Graham Av",
        "route": "L",
        "feed": "l",
        "platforms": {"N": "L11N", "S": "L11S"},
        "labels": {"N": "Manhattan-bound (8 Av)", "S": "Canarsie-bound"},
    },
]

CACHE_TTL_SECONDS = 10
_feed_cache = {}


def fetch_feed(feed_key):
    cached = _feed_cache.get(feed_key)
    now = time.time()
    if cached and now - cached["fetched_at"] < CACHE_TTL_SECONDS:
        return cached["feed"]

    resp = requests.get(FEED_URLS[feed_key], timeout=10)
    resp.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(resp.content)
    _feed_cache[feed_key] = {"feed": feed, "fetched_at": now}
    return feed


def arrivals_for_stop(feed, stop_id, now_epoch, limit=5):
    results = []
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        trip_update = entity.trip_update
        for stu in trip_update.stop_time_update:
            if stu.stop_id != stop_id:
                continue
            if stu.HasField("arrival"):
                event_time = stu.arrival.time
            elif stu.HasField("departure"):
                event_time = stu.departure.time
            else:
                continue
            minutes_away = (event_time - now_epoch) / 60
            if minutes_away < -0.5:
                continue
            results.append(
                {
                    "route": trip_update.trip.route_id,
                    "trip_id": trip_update.trip.trip_id,
                    "minutes_away": round(max(minutes_away, 0), 1),
                    "arrival_epoch": event_time,
                }
            )
    results.sort(key=lambda r: r["minutes_away"])
    return results[:limit]


@app.route("/api/arrivals")
def api_arrivals():
    now_epoch = time.time()
    stations_out = []
    for station in STATIONS:
        feed = fetch_feed(station["feed"])
        directions_out = {}
        for direction, stop_id in station["platforms"].items():
            directions_out[direction] = {
                "label": station["labels"][direction],
                "arrivals": arrivals_for_stop(feed, stop_id, now_epoch),
            }
        stations_out.append(
            {
                "stop_id": station["stop_id"],
                "name": station["name"],
                "route": station["route"],
                "directions": directions_out,
            }
        )
    return jsonify({"generated_at": now_epoch, "stations": stations_out})


@app.route("/api/routes")
def api_routes():
    return jsonify(ROUTE_COLORS)


@app.route("/api/search")
def api_search():
    query = request.args.get("q", "").strip().lower()
    if len(query) < 2:
        return jsonify([])
    matches = [s for s in STATION_INDEX.values() if query in s["name"].lower()]
    matches.sort(key=lambda s: s["name"])
    return jsonify(matches[:8])


@app.route("/api/stations", methods=["POST"])
def api_add_station():
    body = request.get_json(force=True)
    stop_id = body.get("stop_id")
    route = body.get("route")
    station = STATION_INDEX.get(stop_id)
    if not station or route not in station["routes"] or route not in ROUTE_TO_FEED:
        return jsonify({"error": "unknown station or route"}), 400

    already_added = any(s["stop_id"] == stop_id and s["route"] == route for s in STATIONS)
    if not already_added:
        STATIONS.append(
            {
                "stop_id": stop_id,
                "name": station["name"],
                "route": route,
                "feed": ROUTE_TO_FEED[route],
                "platforms": station["platforms"],
                "labels": {"N": "Northbound", "S": "Southbound"},
            }
        )
    return jsonify({"ok": True})


@app.route("/api/stations", methods=["DELETE"])
def api_remove_station():
    body = request.get_json(force=True)
    stop_id = body.get("stop_id")
    route = body.get("route")
    STATIONS[:] = [s for s in STATIONS if not (s["stop_id"] == stop_id and s["route"] == route)]
    return jsonify({"ok": True})


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    app.run(debug=True, port=5001)
