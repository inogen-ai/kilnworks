import "@testing-library/jest-dom/vitest";

// jsdom doesn't implement Element.scrollTo (https://github.com/jsdom/jsdom/issues/1695);
// Chat.tsx calls it to keep the transcript pinned to the bottom on new messages. Stub it
// so components that scroll don't throw in tests that don't care about scroll position.
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {};
}
