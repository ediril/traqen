// Small enhancements on top of Bootstrap (which handles the dialogs, the ⋯
// dropdown, and the phone sidebar): growing text boxes, inline editing, live
// link checks, and the bookmarklet's save window.

document.querySelectorAll("[data-timezone]").forEach((input) => {
  input.value = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
});

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!confirm(form.dataset.confirm)) event.preventDefault();
  });
});

document.querySelectorAll("[data-copy-button]").forEach((button) => {
  button.addEventListener("click", async () => {
    const text = button.parentElement.querySelector("[data-copy]").value;
    await navigator.clipboard.writeText(text);
    button.textContent = "Copied";
    setTimeout(() => (button.textContent = "Copy"), 1500);
  });
});

// The sidebar bookmarklet is meant to be dragged to the bookmarks bar;
// clicking it here would only "save" Traqen itself, so explain instead.
document.querySelectorAll("[data-bookmarklet]").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    link.parentElement.querySelector("[data-bookmarklet-hint]").hidden = false;
  });
});

// Traq text boxes: grow with their text; the corner grip drags them taller,
// and they never shrink below the height chosen that way. ⌘/Ctrl+Enter saves.
document.querySelectorAll("[data-traq-box]").forEach((box) => {
  const text = box.querySelector("textarea");
  let chosenHeight = 0;
  const grow = () => {
    // Heights are border-box: the text's height plus the box's own border.
    const border = text.offsetHeight - text.clientHeight;
    text.style.height = "auto";
    text.style.height = `${Math.max(text.scrollHeight + border, chosenHeight)}px`;
  };
  text.addEventListener("input", grow);
  text.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      if (text.value.trim()) text.form.requestSubmit();
    }
  });
  box.grow = grow;

  const grip = box.querySelector("[data-resize-grip]");
  grip.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    grip.setPointerCapture(event.pointerId);
    const startY = event.clientY;
    const startHeight = text.offsetHeight;
    const drag = (move) => {
      chosenHeight = Math.max(48, startHeight + move.clientY - startY);
      grow();
    };
    const stop = () => {
      grip.removeEventListener("pointermove", drag);
      text.focus();
    };
    grip.addEventListener("pointermove", drag);
    grip.addEventListener("pointerup", stop, { once: true });
    grip.addEventListener("pointercancel", stop, { once: true });
  });
});

// Dialogs (Bootstrap modals): focus the field that matters once one opens.
document.querySelectorAll(".modal").forEach((modal) => {
  modal.addEventListener("shown.bs.modal", () => {
    const field = modal.querySelector("[data-autofocus], textarea");
    field?.focus();
    if (field?.select) field.select();
  });
});

// Sidebar project icons open the shared Rename/Delete dialog; point it at
// the project whose icon was clicked.
["rename-project", "delete-project"].forEach((id) => {
  const modal = document.getElementById(id);
  modal?.addEventListener("show.bs.modal", (event) => {
    const { projectId, projectName } = event.relatedTarget.dataset;
    const form = modal.querySelector("form");
    form.action = form.dataset.projectForm.replace("{id}", projectId);
    modal.querySelectorAll("[data-project-name-field]").forEach((f) => (f.value = projectName));
    modal.querySelectorAll("[data-project-name-text]").forEach((t) => (t.textContent = projectName));
    modal.querySelectorAll("input[type=checkbox]").forEach((c) => (c.checked = false));
  });
});

// "+ New project": swap the project list for a name field; Escape swaps back.
document.querySelectorAll("[data-project-picker]").forEach((picker) => {
  const button = picker.querySelector("[data-new-project]");
  const name = picker.querySelector("[data-new-project-name]");
  const select = picker.querySelector("select");
  const showName = (show) => {
    button.hidden = show;
    if (select) select.hidden = show;
    name.hidden = !show;
    if (!show) name.value = "";
    // The picked project changed: let the form know.
    name.dispatchEvent(new Event("change", { bubbles: true }));
  };
  button.addEventListener("click", () => {
    showName(true);
    name.focus();
  });
  name.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      showName(false);
    }
  });
});

// Editing a traq: Edit (in its ⋯ menu) switches its page to the edit box;
// Cancel switches back.
const setEditing = (editing) => {
  const view = document.querySelector("[data-view]");
  const edit = document.querySelector("[data-edit]");
  view.hidden = editing;
  edit.hidden = !editing;
  if (editing) {
    const box = edit.querySelector("[data-traq-box]");
    box.grow();
    box.querySelector("textarea").focus();
  }
};
document.querySelectorAll("[data-start-edit]").forEach((button) => {
  button.addEventListener("click", () => setEditing(true));
});

// The traq page shows the last known state at once; links due a check are
// checked now, and the content is swapped for the fresh one (unless you've
// started editing by then).
document.querySelectorAll("[data-live-url]").forEach(async (view) => {
  try {
    const response = await fetch(view.dataset.liveUrl);
    if (response.ok && view.hidden === false) view.innerHTML = await response.text();
  } catch {
    // Offline or server down: keep showing the last known state.
  }
});
document.querySelectorAll("[data-toggle-edit]").forEach((button) => {
  button.addEventListener("click", () => setEditing(false));
});

// Switch light / dark, and keep the choice (the page head applies it).
document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
  button.addEventListener("click", () => {
    const root = document.documentElement;
    root.dataset.bsTheme = root.dataset.bsTheme === "dark" ? "light" : "dark";
    try {
      localStorage.setItem("theme", root.dataset.bsTheme);
    } catch {
      // Storage off (e.g. private browsing): it still switches for this page.
    }
  });
});

// Bookmarklet save window
document.querySelectorAll("[data-close]").forEach((button) => {
  // A popup closes; a tab (popup was blocked) goes back to the page.
  const popup = button.form?.querySelector("input[name=popup]");
  button.addEventListener("click", () => (popup ? window.close() : history.back()));
});

document.querySelectorAll("[data-capture]").forEach((form) => {
  const newRadio = form.querySelector("input[name=traq][value=new]");
  form.querySelector("[data-project-picker]").addEventListener("focusin", () => {
    newRadio.checked = true;
  });

  // The server rendered, for each project the picker offers, its recent traqs
  // and (if a traq there has the page) an "Already in" notice. Show the
  // picked project's, and disable Save while a new traq would duplicate it.
  const select = form.querySelector("select[name=project_id]");
  const projectName = form.querySelector("[name=new_project]");
  const alertBox = form.querySelector("[data-dup-alert]");
  const save = form.querySelector("[data-save]");
  const recentSection = form.querySelector("[data-recent-section]");
  const groups = [...form.querySelectorAll("[data-recent-for]")];
  // The picked project as the server keyed it: "" for no project, else its
  // id; null for a project that's being named and doesn't exist yet.
  const pickedKey = () => {
    const name = projectName.hidden ? "" : projectName.value.trim().replace(/\s+/g, " ");
    if (!name) return select ? select.value : "";
    const match = [...(select?.options || [])].find((o) => o.value && o.text === name);
    return match ? match.value : null;
  };
  const visibleGroup = () => groups.find((group) => !group.hidden);
  const update = () => {
    const key = pickedKey();
    let held = false;
    form.querySelectorAll("[data-held-in]").forEach((notice) => {
      notice.hidden = notice.dataset.heldIn !== key;
      held ||= !notice.hidden;
    });
    const blocked = held && newRadio.checked;
    alertBox.hidden = !blocked;
    save.disabled = blocked;
    groups.forEach((group) => {
      group.hidden = group.disabled = group.dataset.recentFor !== key;
    });
    recentSection.hidden = !visibleGroup()?.querySelector("[data-traq]");
    if (!form.querySelector("input[name=traq]:checked:enabled")) newRadio.checked = true;
  };
  form.addEventListener("change", update);
  form.addEventListener("input", update);

  // Find an existing traq to add the page to, searching as you type.
  const search = form.querySelector("[data-traq-search]");
  const results = form.querySelector("[data-traq-results]");
  const noMatches = form.querySelector("[data-no-matches]");
  const pageUrl = form.querySelector("input[name=url]").value;
  let pending;
  search.addEventListener("input", () => {
    clearTimeout(pending);
    pending = setTimeout(async () => {
      const query = search.value;
      const chosen = results.querySelector("input:checked")?.value;
      let rows = "";
      if (query.trim()) {
        let response;
        try {
          response = await fetch(
            `/capture/traqs?q=${encodeURIComponent(query)}&url=${encodeURIComponent(pageUrl)}`,
          );
        } catch {
          return; // offline or server down: keep what's shown
        }
        if (!response.ok || search.value !== query) return; // stale or failed
        rows = await response.text();
      }
      results.innerHTML = rows;
      // Traqs already listed under Recent traqs aren't repeated.
      results.querySelectorAll("[data-traq]").forEach((row) => {
        if (visibleGroup()?.querySelector(`[data-traq="${row.dataset.traq}"]`)) row.remove();
      });
      const found = results.querySelector("[data-traq]");
      results.hidden = !found;
      noMatches.hidden = !query.trim() || Boolean(found);
      // Keep the choice if it's still listed; otherwise fall back to New traq.
      const keep = chosen && results.querySelector(`input[value="${chosen}"]`);
      if (keep) keep.checked = true;
      update();
    }, 150);
  });
  search.addEventListener("keydown", (event) => {
    // Enter picks the top match instead of submitting half-chosen.
    if (event.key === "Enter" && !(event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      const first = results.querySelector("input[type=radio]");
      if (first) first.checked = true;
      update();
    }
  });

  update();

  form.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") window.close();
  });
});
