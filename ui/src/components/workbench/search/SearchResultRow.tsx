import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import type { MessageSearchMatch } from '../../../context/ApiContext';
import { formatRelativeTime } from '../../../lib/relativeTime';
import { Snippet } from './Snippet';

type SearchResultRowProps = {
  match: MessageSearchMatch;
  // Keyboard-highlighted row (palette arrow navigation in P3); paints a
  // mint-soft background + mint ring so the active hit reads clearly.
  selected?: boolean;
  onSelect?: () => void;
};

// One matching message: a role chip (YOU / AGENT), the highlighted snippet, and
// a muted relative timestamp. Presentational + reusable by both the desktop
// palette and the mobile page — navigation is wired by the consumer via
// ``onSelect``.
export const SearchResultRow: React.FC<SearchResultRowProps> = ({ match, selected, onSelect }) => {
  const { t } = useTranslation();
  const isUser = match.author === 'user';

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected ? 'true' : undefined}
      className={clsx(
        'flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition',
        selected
          ? 'bg-mint-soft ring-1 ring-inset ring-mint/40'
          : 'hover:bg-foreground/[0.04]',
      )}
    >
      <span
        className={clsx(
          'shrink-0 rounded-md px-2 py-0.5 font-mono text-[9px] font-bold uppercase tracking-wider',
          isUser
            ? 'bg-cyan-soft text-cyan'
            : 'bg-mint-soft text-mint',
        )}
      >
        {isUser ? t('workbench.search.roleYou') : t('workbench.search.roleAgent')}
      </span>
      <span className="min-w-0 flex-1">
        <Snippet snippet={match.snippet} />
      </span>
      <span className="shrink-0 font-mono text-[10px] text-muted">
        {formatRelativeTime(match.created_at, t)}
      </span>
    </button>
  );
};
