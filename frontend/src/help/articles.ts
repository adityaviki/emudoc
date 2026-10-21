// The help pages' search (src/help/HelpPage.tsx): over the articles the server filled in
// (backend/app/help), in the page, since they are a few pages of text the page holds already.

import type { HelpArticle } from '../api.ts'

/**
 * The articles that hold every word of `query`, in their title or their text, ignoring case:
 * those whose title holds one first, then in the help's own order. Every article for no words.
 */
export function matching(articles: HelpArticle[], query: string): HelpArticle[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return articles
  const found = articles.filter((article) => {
    const text = `${article.title}\n${article.text}`.toLowerCase()
    return words.every((word) => text.includes(word))
  })
  const inTitle = (article: HelpArticle) => words.some((word) => article.title.toLowerCase().includes(word))
  return [...found.filter(inTitle), ...found.filter((article) => !inTitle(article))]
}
