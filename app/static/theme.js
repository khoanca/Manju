// Chạy trước khi vẽ trang (nạp trong <head>, không defer) để giao diện đã chọn
// không bị nháy. Chưa chọn gì = theo chế độ sáng/tối của máy.
(function () {
  try {
    var t = localStorage.getItem("theme");
    if (t === "dark" || t === "light") document.documentElement.setAttribute("data-theme", t);
  } catch (e) {
    /* không đọc được bộ nhớ: trang vẫn theo máy */
  }
})();
function setTheme(t) {
  if (t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
  try {
    if (t === "auto") localStorage.removeItem("theme");
    else localStorage.setItem("theme", t);
  } catch (e) {
    /* lựa chọn chỉ giữ cho lần mở này */
  }
}
function currentTheme() {
  return document.documentElement.getAttribute("data-theme") || "auto";
}
document.addEventListener("DOMContentLoaded", function () {
  var sel = document.getElementById("themeSel");
  if (!sel) return;
  sel.value = currentTheme();
  sel.addEventListener("change", function () { setTheme(sel.value); });
});
