(function () {
  const data = JSON.parse(document.getElementById("incident-data").textContent);
  const mapElement = document.getElementById("incident-map");
  if (mapElement) {
    const map = L.map("incident-map").setView([13.0827, 80.2707], 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors"
    }).addTo(map);
    const colors = {Critical:"#d92d20",High:"#b54708",Medium:"#fdb022",Low:"#067647"};
    data.forEach(function (incident) {
      L.circleMarker([incident.latitude, incident.longitude], {
        radius: 8, color: "#fff", weight: 1,
        fillColor: colors[incident.severity] || "#667085", fillOpacity: 0.9
      }).bindPopup("<strong>" + incident.id + "</strong><br>" + incident.incident_type + "<br>" + incident.status).addTo(map);
    });
  }
  const statusCounts = {};
  data.forEach(function (incident) { statusCounts[incident.status] = (statusCounts[incident.status] || 0) + 1; });
  const statusCanvas = document.getElementById("status-chart");
  if (statusCanvas) {
    new Chart(statusCanvas, {type:"doughnut", data:{labels:Object.keys(statusCounts), datasets:[{data:Object.values(statusCounts), backgroundColor:["#175cd3","#84adff","#fdb022","#d92d20","#12b76a","#667085"]}]}, options:{responsive:true,plugins:{legend:{position:"bottom"}}}});
  }
})();
