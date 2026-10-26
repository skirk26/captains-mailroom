// Shows <time data-local-time> elements in the phone's own time zone.
// The server stores and sends UTC; the text inside each element is the UTC
// fallback that stays if this script doesn't run.

document.querySelectorAll("time[data-local-time]").forEach((element) => {
  const when = new Date(element.dateTime);
  if (Number.isNaN(when.getTime())) return;
  element.textContent = when.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
});
