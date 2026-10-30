document.addEventListener("DOMContentLoaded", function () {
    setupDateDefaults();
    setupConfirm();
    setupFlash();
    setupPrint();
    setupCharts();
    setupNotifications();
});

function setupDateDefaults() {
    var today = new Date();
    var pad = function (value) {
        return String(value).padStart(2, "0");
    };
    var todayValue = today.getFullYear() + "-" + pad(today.getMonth() + 1) + "-" + pad(today.getDate());

    var recordDate = document.getElementById("record_date");
    if (recordDate && !recordDate.value) {
        recordDate.value = todayValue;
    }
}

function setupConfirm() {
    document.querySelectorAll("[data-confirm]").forEach(function (button) {
        button.addEventListener("click", function (event) {
            if (!window.confirm(button.getAttribute("data-confirm"))) {
                event.preventDefault();
            }
        });
    });
}

function setupFlash() {
    document.querySelectorAll(".flash").forEach(function (flash) {
        window.setTimeout(function () {
            flash.style.transition = "opacity 0.4s ease";
            flash.style.opacity = "0";
            window.setTimeout(function () {
                flash.remove();
            }, 400);
        }, 4000);
    });
}

function setupPrint() {
    var printButton = document.getElementById("print-report");
    if (printButton) {
        printButton.addEventListener("click", function () {
            window.print();
        });
    }
}

function setupCharts() {
    var boxes = document.querySelectorAll(".chart-box canvas");
    if (!boxes.length) {
        return;
    }

    if (typeof Chart === "undefined") {
        document.querySelectorAll(".chart-box").forEach(function (box) {
            box.innerHTML = '<p class="empty">圖表元件未載入，請確認 static/vendor/chart.umd.min.js 是否存在。</p>';
        });
        return;
    }

    Chart.defaults.font.family = '"Segoe UI", "Noto Sans TC", "Microsoft JhengHei", Arial, sans-serif';
    Chart.defaults.font.size = 12;
    Chart.defaults.color = "#6b7280";

    var palette = ["#2f5fd0", "#4f9be8", "#12b886", "#f0b429", "#e05d5d", "#8b7cf6", "#38b6b0", "#b0b7c3"];

    if (window.LIFEFLOW_DAILY) {
        var dailyCanvas = document.getElementById("dailyChart");
        if (dailyCanvas) {
            new Chart(dailyCanvas, {
                type: "line",
                data: {
                    labels: window.LIFEFLOW_DAILY.labels,
                    datasets: [{
                        label: "每日支出",
                        data: window.LIFEFLOW_DAILY.totals,
                        borderColor: "#2f5fd0",
                        backgroundColor: "rgba(47, 95, 208, 0.12)",
                        fill: true,
                        tension: 0.35,
                        borderWidth: 2,
                        pointRadius: 2,
                        pointHoverRadius: 5,
                        pointBackgroundColor: "#2f5fd0"
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: "index", intersect: false },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: function (context) {
                                    return " " + context.parsed.y.toLocaleString("zh-TW") + " 元";
                                }
                            }
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            grid: { color: "#eef1f7" },
                            ticks: {
                                callback: function (value) {
                                    return value.toLocaleString("zh-TW");
                                }
                            }
                        },
                        x: {
                            grid: { display: false },
                            ticks: { maxTicksLimit: 10 }
                        }
                    }
                }
            });
        }
    }

    if (window.LIFEFLOW_REPORT) {
        var categoryCanvas = document.getElementById("categoryChart");
        var trendCanvas = document.getElementById("trendChart");
        var report = window.LIFEFLOW_REPORT;

        if (categoryCanvas) {
            new Chart(categoryCanvas, {
                type: "doughnut",
                data: {
                    labels: report.categories.labels,
                    datasets: [{
                        data: report.categories.totals,
                        backgroundColor: palette,
                        borderColor: "#fff",
                        borderWidth: 2
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "58%",
                    plugins: {
                        legend: {
                            position: "bottom",
                            labels: { boxWidth: 10, boxHeight: 10, padding: 12 }
                        },
                        tooltip: {
                            callbacks: {
                                label: function (context) {
                                    var total = context.dataset.data.reduce(function (sum, value) {
                                        return sum + value;
                                    }, 0);
                                    var share = total ? (context.parsed / total * 100).toFixed(1) : "0.0";
                                    return " " + context.parsed.toLocaleString("zh-TW") + " 元（" + share + "%）";
                                }
                            }
                        }
                    }
                }
            });
        }

        if (trendCanvas) {
            new Chart(trendCanvas, {
                type: "bar",
                data: {
                    labels: report.trend.labels,
                    datasets: [
                        {
                            label: "收入",
                            data: report.trend.income,
                            backgroundColor: "#12b886",
                            borderRadius: 6
                        },
                        {
                            label: "支出",
                            data: report.trend.spending,
                            backgroundColor: "#e05d5d",
                            borderRadius: 6
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            position: "bottom",
                            labels: { boxWidth: 10, boxHeight: 10, padding: 12 }
                        },
                        tooltip: {
                            callbacks: {
                                label: function (context) {
                                    return " " + context.dataset.label + " " + context.parsed.y.toLocaleString("zh-TW") + " 元";
                                }
                            }
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            grid: { color: "#eef1f7" },
                            ticks: {
                                callback: function (value) {
                                    return value.toLocaleString("zh-TW");
                                }
                            }
                        },
                        x: { grid: { display: false } }
                    }
                }
            });
        }
    }
}

function setupNotifications() {
    var notifyButton = document.getElementById("enable-notify");
    if (!notifyButton) {
        return;
    }

    if (!("Notification" in window)) {
        notifyButton.disabled = true;
        notifyButton.textContent = "此瀏覽器不支援通知";
        return;
    }

    var updateLabel = function () {
        if (Notification.permission === "granted") {
            notifyButton.textContent = "提醒已開啟";
        } else if (Notification.permission === "denied") {
            notifyButton.textContent = "通知已被封鎖";
        } else {
            notifyButton.textContent = "開啟提醒";
        }
    };

    var showReminder = function () {
        if (Notification.permission !== "granted") {
            return;
        }

        fetch("/api/reminders", { headers: { Accept: "application/json" } })
            .then(function (response) {
                return response.ok ? response.json() : null;
            })
            .then(function (data) {
                if (!data) {
                    return;
                }

                var lines = [];
                if (data.over_budget) {
                    lines.push("本月支出已超過預算（使用率 " + data.usage + "%）");
                }
                data.overdue.forEach(function (task) {
                    lines.push("已逾期：" + task.title + "（" + task.due_date + "）");
                });
                data.due_today.forEach(function (task) {
                    lines.push("今日到期：" + task.title);
                });

                if (!lines.length) {
                    return;
                }

                var key = "lifeflow-reminder-" + data.month + "-" + new Date().toISOString().slice(0, 10);
                if (window.localStorage.getItem(key)) {
                    return;
                }

                new Notification("LifeFlow 生活提醒", {
                    body: lines.slice(0, 4).join("\n"),
                    tag: "lifeflow-reminder"
                });
                window.localStorage.setItem(key, "1");
            })
            .catch(function () {
                return null;
            });
    };

    notifyButton.addEventListener("click", function () {
        if (Notification.permission === "granted") {
            showReminder();
            return;
        }
        Notification.requestPermission().then(function () {
            updateLabel();
            showReminder();
        });
    });

    updateLabel();
    showReminder();
}
