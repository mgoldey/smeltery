/** Save `text` as a file named `name` through a temporary blob URL.
 *
 * The anchor goes into the document and the blob URL is revoked in a later task: Firefox starts the download
 * after the click handler returns, and revoking in the same task leaves it empty or never started.
 */
export function downloadText(name: string, text: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    a.remove();
    URL.revokeObjectURL(url);
  }, 0);
}
