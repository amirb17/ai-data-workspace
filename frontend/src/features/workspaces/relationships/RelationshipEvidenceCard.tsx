import type { Candidate, RelationshipEvidence } from '../../../services/api/relationships'
const ratio = (value: number | null) => value === null ? 'Unavailable' : `${(value*100).toFixed(2)}%`
export function RelationshipEvidenceCard({evidence: e}: {evidence: RelationshipEvidence}) {
  const s = e.signals, o = s.overlap
  return <>
    <p className="break-words font-medium">{e.parent.dataset_name}.{e.parent.columns.join(' + ')} <span aria-label="parent to child">→</span> {e.child.dataset_name}.{e.child.columns.join(' + ')}</p>
    <p className="mt-2 text-sm">Likely {e.candidate_cardinality.replaceAll('_',' ').toLowerCase()} · Parent-key candidate</p>
    <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
      <div><dt className="text-gray-500">Canonical types</dt><dd>{s.parent_type} / {s.child_type} · no coercion</dd></div>
      <div><dt className="text-gray-500">Configured parent business key</dt><dd>{s.configured_parent_key ? 'Yes' : 'No — observed unique-side candidate'}</dd></div>
      <div><dt className="text-gray-500">Parent uniqueness / nulls</dt><dd>{ratio(s.parent.uniqueness_ratio)} / {ratio(s.parent.null_ratio)} · {s.parent.duplicate_rows} repeated rows</dd></div>
      <div><dt className="text-gray-500">Child uniqueness / null references</dt><dd>{ratio(s.child.uniqueness_ratio)} / {ratio(s.child.null_ratio)} · {s.child.duplicate_rows} repeated rows</dd></div>
      <div><dt className="text-gray-500">Non-null child distinct-key coverage</dt><dd>{ratio(o.child_to_parent_coverage)} · {o.matched_distinct_keys} matched / {o.child_non_null_distinct_keys} distinct · {o.missing_distinct_keys} missing</dd></div>
      <div><dt className="text-gray-500">Parent keys referenced</dt><dd>{ratio(o.parent_referenced_ratio)} · {o.method.replaceAll('_',' ')}</dd></div>
    </dl>
    <p className="mt-3 text-sm">Deterministic evidence score: {e.deterministic_score}/100 · not a probability</p>
    <ul className="mt-2 space-y-1 text-sm text-gray-600">{e.score_components.map(c=><li key={c.name}>{c.name}: {c.points} points — {c.explanation}</li>)}</ul>
    <p className="mt-3 text-sm">Semantic roles at verification: {s.parent_semantic_role ?? 'Unavailable'} / {s.child_semantic_role ?? 'Unavailable'}</p>
    <ul className="mt-2 space-y-1 text-sm text-amber-800">{e.warnings.map(w=><li key={w}>{w}</li>)}</ul>
    <p className="mt-3 text-xs text-gray-500">{e.source_pins.map(p=>`Dataset ${p.dataset_id}: state ${p.state_version}, profile ${p.profile_version}`).join(' · ')}</p>
  </>
}
export function CandidateCard({candidate,busy,review}: {candidate: Candidate; busy: boolean; review: (candidate: Candidate, action: 'confirm' | 'reject') => void}) {
  return <article className="min-w-0 rounded-xl border border-gray-200 bg-white p-5">
    <p className="mb-3 text-sm font-semibold">Suggested relationship · {candidate.review_status.replaceAll('_',' ')}</p>
    <RelationshipEvidenceCard evidence={candidate.evidence}/>
    <div className="mt-4 flex flex-wrap gap-3">
      <button className="min-h-11 rounded-lg bg-indigo-600 px-4 py-2 text-white disabled:opacity-50" disabled={busy || !candidate.evidence.can_confirm || candidate.review_status==='CONFIRMED'} onClick={()=>review(candidate,'confirm')}>Confirm</button>
      <button className="min-h-11 rounded-lg border px-4 py-2 disabled:opacity-50" disabled={busy || candidate.review_status==='REJECTED'} onClick={()=>review(candidate,'reject')}>Reject</button>
    </div>
    {!candidate.evidence.can_confirm && <p className="mt-2 text-sm text-amber-800">Confirmation requires a unique non-null parent and complete non-null child coverage in V1.</p>}
  </article>
}
