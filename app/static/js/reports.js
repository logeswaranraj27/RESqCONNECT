(function () {
  const overview = JSON.parse(document.getElementById("overview-data").textContent);
  function render(id, labels, values, colors) {
    const canvas = document.getElementById(id);
    if (!canvas) return;
    new Chart(canvas, {type:"bar", data:{labels:labels,datasets:[{data:values,backgroundColor:colors,borderRadius:5}]}, options:{responsive:true,plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,ticks:{precision:0}}}}});
  }
  const type = overview.by_type || {};
  render("type-chart", Object.keys(type), Object.values(type), ["#175cd3","#84adff","#0ba5ec","#12b76a"]);
  const severity = overview.by_severity || {};
  render("severity-chart", Object.keys(severity), Object.values(severity), ["#667085","#fdb022","#b54708","#d92d20"]);
})();
