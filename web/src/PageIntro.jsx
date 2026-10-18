/** A short, plain-language header for a page: what it shows, how to read it, what to try first. Written for someone who has never seen Kubernetes or a graph. */
export default function PageIntro({ title, what, read, tryIt, compact = false }) {
  if (compact) {  // after the page is in use, the explanation folds into one line that can be reopened
    return (
      <details className="page-intro compact">
        <summary><strong>{title}</strong> <span className="muted">· how to read this page</span></summary>
        <p className="what">{what}</p>
        <dl>{read && <><dt>How to read it</dt><dd>{read}</dd></>}{tryIt && <><dt>Try this</dt><dd>{tryIt}</dd></>}</dl>
      </details>
    )
  }
  return (
    <section className="page-intro" aria-label={`About the ${title} page`}>
      <h2>{title}</h2>
      <p className="what">{what}</p>
      <dl>
        {read && <><dt>How to read it</dt><dd>{read}</dd></>}
        {tryIt && <><dt>Try this</dt><dd>{tryIt}</dd></>}
      </dl>
    </section>
  )
}
