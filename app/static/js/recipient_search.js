// Recipient autofill on the Confirm Check-In page: suggests people entered
// before as staff type a last name or box number. A convenience only; if the
// search finds nothing or fails, the form works exactly as if this weren't here.

(() => {
  const DEBOUNCE_MS = 250;
  const MIN_LENGTH = 2; // the server returns nothing for shorter queries anyway

  const form = document.getElementById("checkin-form");
  const firstName = document.getElementById("first-name");
  const lastName = document.getElementById("last-name");
  const boxNumber = document.getElementById("box-number");

  // "Rivera, Alex (Box 1234)", dropping whichever parts are missing
  function describe(recipient) {
    const name = [recipient.last_name, recipient.first_name].filter(Boolean).join(", ");
    const box = recipient.mailbox_number ? `Box ${recipient.mailbox_number}` : "";
    return name && box ? `${name} (${box})` : name || box;
  }

  function attachSuggestions(input) {
    const list = document.createElement("div");
    list.id = `${input.id}-suggestions`;
    list.className = "list-group suggest-list";
    list.setAttribute("role", "listbox");
    list.hidden = true;
    input.after(list); // style.css floats it directly under the input

    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-controls", list.id);
    input.setAttribute("aria-expanded", "false");

    let timer = null;
    let request = null; // AbortController for the search in flight
    let highlighted = -1;

    function cancelSearch() {
      clearTimeout(timer);
      if (request) request.abort();
      request = null;
    }

    function hideList() {
      list.hidden = true;
      list.replaceChildren();
      highlighted = -1;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
    }

    function close() {
      cancelSearch();
      hideList();
    }

    function choose(recipient) {
      firstName.value = recipient.first_name || "";
      lastName.value = recipient.last_name || "";
      boxNumber.value = recipient.mailbox_number || "";
      close();
    }

    function highlight(index) {
      const items = [...list.children];
      highlighted = (index + items.length) % items.length;
      items.forEach((item, i) => {
        item.classList.toggle("is-highlighted", i === highlighted);
        item.setAttribute("aria-selected", String(i === highlighted));
      });
      input.setAttribute("aria-activedescendant", items[highlighted].id);
      items[highlighted].scrollIntoView({ block: "nearest" });
    }

    function showList(recipients) {
      const items = recipients.map((recipient, i) => {
        const item = document.createElement("button");
        item.type = "button";
        item.id = `${list.id}-${i}`;
        item.className = "list-group-item list-group-item-action";
        item.tabIndex = -1; // reached with the arrow keys, not Tab
        item.setAttribute("role", "option");
        item.textContent = describe(recipient); // textContent: a name can never inject HTML
        item.addEventListener("click", () => choose(recipient));
        return item;
      });
      list.replaceChildren(...items);
      highlighted = -1;
      list.hidden = items.length === 0;
      input.setAttribute("aria-expanded", String(!list.hidden));
    }

    async function search(query) {
      request = new AbortController();
      const url = new URL(form.dataset.searchUrl, window.location.href);
      url.searchParams.set("q", query);
      try {
        const response = await fetch(url, {
          signal: request.signal,
          headers: { Accept: "application/json" },
        });
        // A signed-out session redirects to the login page, which isn't JSON
        if (!response.ok || response.redirected) throw new Error(`search returned ${response.status}`);
        const recipients = await response.json();
        if (document.activeElement === input && Array.isArray(recipients)) {
          showList(recipients);
        }
      } catch (err) {
        if (err.name !== "AbortError") {
          console.warn("Recipient search failed:", err);
          hideList();
        }
      }
    }

    input.addEventListener("input", () => {
      cancelSearch(); // results for what they typed before are stale now
      const query = input.value.trim();
      if (query.length < MIN_LENGTH) {
        hideList();
        return;
      }
      timer = setTimeout(() => search(query), DEBOUNCE_MS);
    });

    input.addEventListener("keydown", (event) => {
      if (list.hidden) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        highlight(highlighted + 1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        highlight(highlighted < 0 ? -1 : highlighted - 1);
      } else if (event.key === "Enter" && highlighted >= 0) {
        event.preventDefault(); // pick the suggestion instead of submitting the form
        list.children[highlighted].click();
      } else if (event.key === "Escape") {
        event.preventDefault();
        close();
      }
    });

    input.addEventListener("blur", close);

    // Tapping a suggestion would otherwise blur the input first, closing the
    // list before the tap lands. This keeps focus (and the phone keyboard) put.
    list.addEventListener("mousedown", (event) => event.preventDefault());
  }

  attachSuggestions(lastName);
  attachSuggestions(boxNumber);
})();
