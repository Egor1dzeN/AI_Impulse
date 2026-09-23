// A sent message carries its attachments inline for the model: document
// text blocks (backend documents.format_for_chat) and one-line notes for
// datasets loaded into memory. The chat bubble shows them as file chips
// instead — this splits a stored message back into typed text + names,
// so history reloaded from the server renders the same way.
const DOCUMENT_BLOCK = /=== Вложенный документ «(.+?)»[\s\S]*?=== Конец документа ===/g;
const DATASET_NOTE = /=== Файл «(.+?)»[^\n]*===/g;

export function datasetNote(name, sourceLabel, count) {
  return `=== Файл «${name}» (${sourceLabel}, записей: ${count}) загружен в долговременную память пользователя ===`;
}

export function splitAttachments(content) {
  const files = [];
  const text = (content || '')
    .replace(DOCUMENT_BLOCK, (_, name) => {
      files.push(name);
      return '';
    })
    .replace(DATASET_NOTE, (_, name) => {
      files.push(name);
      return '';
    })
    .trim();
  return { text, files };
}
