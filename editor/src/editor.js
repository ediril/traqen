// The traq editor: a Tiptap document that loads and saves Markdown, limited to
// what Markdown can hold so saving and reopening gives back what you wrote.

import { Editor, Extension } from "@tiptap/core";
import { Image } from "@tiptap/extension-image";
import { Link } from "@tiptap/extension-link";
import { TaskItem, TaskList } from "@tiptap/extension-list";
import { TableCell, TableHeader, TableKit } from "@tiptap/extension-table";
import { Placeholder } from "@tiptap/extensions";
import { Markdown } from "@tiptap/markdown";
import { StarterKit } from "@tiptap/starter-kit";

// A link that reads as its own address is written as the bare address: a line
// that's just a link is how Traqen knows to show it as a card. Tiptap writes
// it as [address](address) with the text escaped, so it's rewritten after.
const LINK = /\[((?:[^\]\\]|\\.)*)\]\(((?:[^()\s]|\([^()\s]*\))+)\)/g;
const ESCAPED = /\\([\\`*_{}[\]()#+\-.!|~<>])/g;
const BARE_URL = /^https?:\/\/[^\s<>]*[^\s<>.,:;!?'")\]*_~]$/;
const CODE_SPAN = /(`+)[\s\S]*?\1/g;

function bareLink(markdown, text, href) {
  if (text.replace(ESCAPED, "$1") !== href) return markdown;
  if (BARE_URL.test(href)) return href;
  return /[\s<>]/.test(href) ? markdown : `<${href}>`;
}

function bareLinks(markdown) {
  let fence = null;
  return markdown
    .split("\n")
    .map((line) => {
      const opener = line.match(/^\s*(`{3,}|~{3,})/);
      if (fence) {
        if (opener && opener[1][0] === fence[0] && opener[1].length >= fence.length) fence = null;
        return line;
      }
      if (opener) {
        fence = opener[1];
        return line;
      }
      let out = "";
      let at = 0;
      for (const code of line.matchAll(CODE_SPAN)) {
        out += line.slice(at, code.index).replace(LINK, bareLink) + code[0];
        at = code.index + code[0].length;
      }
      return out + line.slice(at).replace(LINK, bareLink);
    })
    .join("\n");
}

/** The editor's document as Traqen stores it. */
export const markdownOf = (editor) => bareLinks(editor.getMarkdown()).trim();

// A Markdown table cell holds one line of text, not lists or paragraphs.
const OneLineCell = TableCell.extend({ content: "paragraph" });
const OneLineHeader = TableHeader.extend({ content: "paragraph" });

const SaveShortcut = Extension.create({
  name: "saveShortcut",
  addOptions: () => ({ save: () => {} }),
  addKeyboardShortcuts() {
    return { "Mod-Enter": () => (this.options.save(), true) };
  },
});

const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];

// Toolbar buttons (data-command) and what each does; `active` lights the
// button up while the selection has that format.
const COMMANDS = {
  bold: { run: (c) => c.toggleBold(), active: ["bold"] },
  italic: { run: (c) => c.toggleItalic(), active: ["italic"] },
  strike: { run: (c) => c.toggleStrike(), active: ["strike"] },
  code: { run: (c) => c.toggleCode(), active: ["code"] },
  heading: { run: (c) => c.toggleHeading({ level: 2 }), active: ["heading", { level: 2 }] },
  bulletList: { run: (c) => c.toggleBulletList(), active: ["bulletList"] },
  orderedList: { run: (c) => c.toggleOrderedList(), active: ["orderedList"] },
  taskList: { run: (c) => c.toggleTaskList(), active: ["taskList"] },
  blockquote: { run: (c) => c.toggleBlockquote(), active: ["blockquote"] },
  table: { run: (c) => c.insertTable({ rows: 3, cols: 3, withHeaderRow: true }) },
  addRow: { run: (c) => c.addRowAfter() },
  addColumn: { run: (c) => c.addColumnAfter() },
  deleteRow: { run: (c) => c.deleteRow() },
  deleteColumn: { run: (c) => c.deleteColumn() },
  deleteTable: { run: (c) => c.deleteTable() },
  undo: { run: (c) => c.undo() },
  redo: { run: (c) => c.redo() },
};

/**
 * Turn a traq's edit form into the editor. The form's textarea stays the field
 * that's sent: it gets the Markdown when the form is submitted.
 */
export function mountEditor(form) {
  const field = form.querySelector("textarea[name=text]");
  const toolbar = form.querySelector("[data-editor-toolbar]");
  const mount = form.querySelector("[data-editor-mount]");
  const csrf = form.querySelector("input[name=csrf]").value;

  const upload = async (file) => {
    if (!IMAGE_TYPES.includes(file.type)) return;
    const body = new FormData();
    body.append("csrf", csrf);
    body.append("image", file);
    let response;
    try {
      response = await fetch(form.dataset.uploadUrl, { method: "POST", body });
    } catch {
      return alert("Couldn’t add the image: Traqen can’t be reached.");
    }
    if (!response.ok) return alert(`Couldn’t add “${file.name}”: ${UPLOAD_ERRORS[response.status] ?? "something went wrong."}`);
    const { url } = await response.json();
    const image = { type: "image", attrs: { src: url, alt: file.name.replace(/\.\w+$/, "") } };
    editor.chain().focus().insertContentAt(imagePlace(editor), image).run();
  };
  const uploadAll = (files) => {
    const images = [...(files ?? [])].filter((file) => IMAGE_TYPES.includes(file.type));
    images.forEach(upload);
    return images.length > 0;
  };

  const editor = new Editor({
    element: mount,
    content: field.value,
    contentType: "markdown",
    extensions: [
      StarterKit.configure({ underline: false, link: false }),
      Link.configure({ openOnClick: false, autolink: true, linkOnPaste: true }),
      TaskList,
      TaskItem.configure({ nested: true }),
      TableKit.configure({ table: { resizable: false }, tableCell: false, tableHeader: false }),
      OneLineCell,
      OneLineHeader,
      Image,
      Placeholder.configure({ placeholder: field.placeholder }),
      Markdown,
      SaveShortcut.configure({ save: () => form.requestSubmit() }),
    ],
    editorProps: {
      attributes: { class: "traq-editor-doc", "aria-label": "Traq text" },
      handlePaste: (_view, event) => uploadAll(event.clipboardData?.files),
      handleDrop: (_view, event) => uploadAll(event.dataTransfer?.files),
    },
  });

  const filePicker = Object.assign(document.createElement("input"), {
    type: "file",
    accept: IMAGE_TYPES.join(","),
    multiple: true,
  });
  filePicker.addEventListener("change", () => {
    uploadAll(filePicker.files);
    filePicker.value = "";
  });

  toolbar.addEventListener("mousedown", (event) => event.preventDefault()); // keep the selection
  toolbar.addEventListener("click", (event) => {
    const button = event.target.closest("[data-command]");
    if (!button) return;
    const name = button.dataset.command;
    if (name === "image") return filePicker.click();
    if (name === "link") return editLink(editor);
    COMMANDS[name].run(editor.chain().focus()).run();
  });

  const showState = () => {
    toolbar.querySelectorAll("[data-command]").forEach((button) => {
      const command = COMMANDS[button.dataset.command];
      if (command?.active) button.setAttribute("aria-pressed", editor.isActive(...command.active));
    });
    toolbar.querySelector("[data-command=link]")?.setAttribute("aria-pressed", editor.isActive("link"));
    toolbar.querySelector("[data-table-tools]").hidden = !editor.isActive("table");
  };
  editor.on("transaction", showState);
  showState();

  form.addEventListener("submit", () => {
    field.value = markdownOf(editor);
  });
  field.closest("[data-traq-box]").hidden = true;
  toolbar.hidden = false;
  mount.hidden = false;
  return editor;
}

const UPLOAD_ERRORS = {
  413: "images can be up to 5 MB.",
  415: "only PNG, JPEG, GIF, and WebP images can be added.",
};

// Where an image goes: at the cursor, or just below the table the cursor is
// in (a Markdown table cell can't hold an image).
function imagePlace(editor) {
  const { $from } = editor.state.selection;
  for (let depth = $from.depth; depth > 0; depth--) {
    if ($from.node(depth).type.name === "table") return $from.after(depth);
  }
  const { from, to } = editor.state.selection;
  return { from, to };
}

function editLink(editor) {
  const current = editor.getAttributes("link").href ?? "";
  const href = prompt("Link to", current || "https://");
  if (href === null) return;
  const chain = editor.chain().focus().extendMarkRange("link");
  if (!href.trim() || href.trim() === "https://") return chain.unsetLink().run();
  if (editor.state.selection.empty && !editor.isActive("link")) {
    return chain.insertContent({ type: "text", text: href, marks: [{ type: "link", attrs: { href } }] }).run();
  }
  chain.setLink({ href: href.trim() }).run();
}
