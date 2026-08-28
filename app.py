import time

import requests
from flask import Flask, jsonify, send_from_directory
from google.transit import gtfs_realtime_pb2

app = Flask(__name__, static_folder="static")

FEED_URLS = {
    "1": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs",
    "l": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-l",
}

STATIONS = [
    {
        "name": "116 St-Columbia University",
        "route": "1",
        "feed": "1",
        "platforms": {"N": "117N", "S": "117S"},
        "labels": {"N": "Uptown", "S": "Downtown"},
    },
    {
        "name": "59 St-Columbus Circle",
        "route": "1",
        "feed": "1",
        "platforms": {"N": "125N", "S": "125S"},
        "labels": {"N": "Uptown", "S": "Downtown"},
    },
    {
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
                "name": station["name"],
                "route": station["route"],
                "directions": directions_out,
            }
        )
    return jsonify({"generated_at": now_epoch, "stations": stations_out})


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    app.run(debug=True, port=5001)
