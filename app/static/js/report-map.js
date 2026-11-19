(function () {
  const defaultLocation = [13.0827, 80.2707];
  const map = L.map("report-map").setView(defaultLocation, 11);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors"
  }).addTo(map);
  let marker = null;
  function setLocation(lat, lng) {
    document.getElementById("latitude").value = lat.toFixed(6);
    document.getElementById("longitude").value = lng.toFixed(6);
  }
  map.on("click", function (event) {
    if (marker) marker.setLatLng(event.latlng);
    else marker = L.marker(event.latlng).addTo(map);
    setLocation(event.latlng.lat, event.latlng.lng);
  });
  setLocation(defaultLocation[0], defaultLocation[1]);
  marker = L.marker(defaultLocation).addTo(map);
})();
