/**
 * Moments — the agent's work as a scrubbable sequence.
 *
 * The lifecycle stream is chatty and low-level: dozens of terminal chunks for
 * one command, a start and a complete for every tool call. That granularity is
 * right for a log and wrong for something you scrub through. A *moment* is one
 * thing a person would say the agent did — "captured arxiv.org", "wrote
 * paper.tex", "ran pdflatex" — with enough payload attached to re-render it
 * later.
 *
 * Two reductions do most of the work:
 *   - terminal chunks coalesce into one moment per command, accumulating text
 *   - a tool's start and complete collapse into a single moment that gains a
 *     result rather than producing a second entry
 */
import { activityKindFor, type ActivityKind } from '@/libs/activity';
import type { Message } from '@/libs/chat-messages/types';

export type PlanStep = {
  index: number;
  text: string;
  status: string;
  notes: string;
  active: boolean;
};

export type PlanProgress = { completed: number; total: number; pct: number };

export type MomentPayload =
  | { type: 'screenshot'; url?: string; title?: string; screenshot: string }
  | {
      type: 'diff';
      path: string;
      added: number;
      deleted: number;
      lines: string[];
      tool?: string;
    }
  | { type: 'terminal'; text: string; name?: string }
  | {
      type: 'tool';
      name: string;
      toolId?: string;
      args?: unknown;
      result?: unknown;
      /** Streamed stdout/stderr, when this tool produced any. */
      output?: string;
      running: boolean;
    }
  | { type: 'plan'; title: string; steps: PlanStep[]; progress: PlanProgress }
  | {
      type: 'fanout';
      reasoning: string;
      workers: { worker_id: string; title: string; kind: string; brief: string; scope: string }[];
    }
  | {
      type: 'worker';
      workerId: string;
      workerTitle: string;
      kind: string;
      brief?: string;
      scope?: string;
      summary?: string;
      status: string;
      verdict?: string;
      reviewReason?: string;
    }
  | { type: 'note'; text: string; tone: 'neutral' | 'error' | 'success' };

export type Moment = {
  id: string;
  kind: ActivityKind;
  at?: Date;
  /** One line, past tense where the work is done. */
  title: string;
  /** Optional second line — a path, a URL, a count. */
  detail?: string;
  /** True while the underlying work is still in flight. */
  running: boolean;
  payload: MomentPayload;
};

const asString = (value: unknown): string => (value == null ? '' : String(value));

const asNumber = (value: unknown): number => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

/** Trim a workspace path down to something readable in a narrow panel. */
export const shortPath = (path: string): string => {
  const cleaned = path.replace(/^\/?(app\/)?workspace\/?/, '').replace(/^\/+/, '');
  const parts = cleaned.split('/').filter(Boolean);
  return parts.length <= 2 ? cleaned : `…/${parts.slice(-2).join('/')}`;
};

/** Host portion of a URL, for labelling browser captures. */
const hostOf = (url: string): string => {
  try {
    return new URL(url).host;
  } catch {
    return url.slice(0, 40);
  }
};

/**
 * Reduce a lifecycle message list into moments, in chronological order.
 */
export const deriveMoments = (messages: Message[]): Moment[] => {
  const moments: Moment[] = [];
  /** Tool call id → index in `moments`, so a complete can find its start. */
  const toolIndexById = new Map<string, number>();
  /** Tool call id → index of the terminal moment currently accumulating. */
  const terminalIndexById = new Map<string, number>();
  /** Index of the single plan moment, which updates in place. */
  let planIndex = -1;
  /** Worker id → index, so completion and review fold into the same moment. */
  const workerIndexById = new Map<string, number>();

  messages.forEach((message, position) => {
    const type = asString(message.type);
    const content = (message.content ?? {}) as Record<string, unknown>;
    const at = message.createdAt;
    const id = asString(message.index || `m-${position}`);

    switch (type) {
      case 'agent:lifecycle:step:think:browser:browse:complete': {
        const screenshot = asString(content.screenshot);
        if (!screenshot) break;
        const url = asString(content.url);
        moments.push({
          id,
          kind: 'browser',
          at,
          title: url ? `Captured ${hostOf(url)}` : 'Captured page',
          detail: asString(content.title) || url,
          running: false,
          payload: { type: 'screenshot', url, title: asString(content.title), screenshot },
        });
        break;
      }

      case 'agent:lifecycle:step:act:tool:file:updated': {
        const path = asString(content.path);
        const added = asNumber(content.added_lines);
        const deleted = asNumber(content.deleted_lines);
        const preview = content.diff_preview as { lines?: unknown } | undefined;
        const lines = Array.isArray(preview?.lines) ? (preview.lines as string[]) : [];
        moments.push({
          id,
          kind: 'file',
          at,
          title: `Wrote ${shortPath(path) || 'file'}`,
          detail: `+${added} −${deleted}`,
          running: false,
          payload: {
            type: 'diff',
            path,
            added,
            deleted,
            lines,
            tool: asString(content.tool) || undefined,
          },
        });
        break;
      }

      case 'agent:lifecycle:step:act:tool:terminal:output': {
        const chunk = asString(content.chunk);
        if (!chunk) break;
        const toolId = asString(content.id) || asString(content.name) || 'terminal';

        // Cap accumulation: a runaway build log should not become a
        // multi-megabyte string held for the life of the page.
        const append = (previous: string) => (previous + chunk).slice(-20000);

        // Prefer folding output into the tool call that produced it. One
        // command should be one moment — the filmstrip previously showed
        // "Ran bash" and its output as two separate, half-empty entries.
        const toolIndex = toolIndexById.get(toolId);
        if (toolIndex !== undefined) {
          const moment = moments[toolIndex];
          if (moment.payload.type === 'tool') {
            moment.payload.output = append(moment.payload.output ?? '');
            moment.kind = 'terminal';
          }
          break;
        }

        const existing = terminalIndexById.get(toolId);
        if (existing !== undefined) {
          const moment = moments[existing];
          if (moment.payload.type === 'terminal') {
            moment.payload.text = append(moment.payload.text);
          }
          break;
        }

        const name = asString(content.name) || 'terminal';
        moments.push({
          id,
          kind: 'terminal',
          at,
          title: `Ran ${name}`,
          running: false,
          payload: { type: 'terminal', text: chunk, name },
        });
        terminalIndexById.set(toolId, moments.length - 1);
        break;
      }

      case 'agent:lifecycle:step:act:tool:execute:start': {
        const name = asString(content.name);
        if (!name || name === 'terminate') break;
        const toolId = asString(content.id) || name;
        moments.push({
          id,
          kind: activityKindFor(type, name),
          at,
          title: `Running ${name.replace(/[_-]+/g, ' ')}`,
          running: true,
          payload: {
            type: 'tool',
            name,
            toolId,
            args: content.arguments ?? content.args,
            running: true,
          },
        });
        toolIndexById.set(toolId, moments.length - 1);
        break;
      }

      case 'agent:lifecycle:step:act:tool:execute:complete': {
        const name = asString(content.name);
        const toolId = asString(content.id) || name;
        const index = toolIndexById.get(toolId);
        // A completing tool closes the terminal moment it was feeding, so a
        // later command starts a fresh block instead of appending forever.
        terminalIndexById.delete(toolId);
        if (index === undefined) break;
        const moment = moments[index];
        moment.running = false;
        moment.title = `Ran ${name.replace(/[_-]+/g, ' ')}`;
        if (moment.payload.type === 'tool') {
          moment.payload.result = content.result;
          moment.payload.running = false;
        }
        break;
      }

      case 'agent:plan:updated': {
        if (content.deleted) break;
        const steps = (Array.isArray(content.steps) ? content.steps : []) as PlanStep[];
        const progress = (content.progress as PlanProgress) ?? {
          completed: 0,
          total: steps.length,
          pct: 0,
        };
        const payload: MomentPayload = {
          type: 'plan',
          title: asString(content.title) || 'Plan',
          steps,
          progress,
        };
        const title = `Planned ${steps.length} step${steps.length === 1 ? '' : 's'}`;
        const detail = `${progress.completed}/${progress.total} done`;
        if (planIndex >= 0) {
          moments[planIndex] = { ...moments[planIndex], at, title, detail, payload };
          break;
        }
        moments.push({ id, kind: 'think', at, title, detail, running: false, payload });
        planIndex = moments.length - 1;
        break;
      }

      case 'agent:lifecycle:orchestration:orchestration_planned': {
        const workers = (Array.isArray(content.workers) ? content.workers : []) as {
          worker_id: string;
          title: string;
          kind: string;
          brief: string;
          scope: string;
        }[];
        moments.push({
          id,
          kind: 'think',
          at,
          title: `Split into ${workers.length} parallel task${workers.length === 1 ? '' : 's'}`,
          detail: asString(content.reasoning) || undefined,
          running: false,
          payload: {
            type: 'fanout',
            reasoning: asString(content.reasoning),
            workers,
          },
        });
        break;
      }

      case 'agent:lifecycle:orchestration:worker_started': {
        const workerId = asString(content.worker_id);
        moments.push({
          id,
          kind: activityKindFor(type, asString(content.kind)),
          at,
          title: asString(content.title) || workerId,
          detail: 'working',
          running: true,
          payload: {
            type: 'worker',
            workerId,
            workerTitle: asString(content.title) || workerId,
            kind: asString(content.kind),
            brief: asString(content.brief),
            scope: asString(content.scope),
            status: 'running',
          },
        });
        workerIndexById.set(workerId, moments.length - 1);
        break;
      }

      case 'agent:lifecycle:orchestration:worker_completed':
      case 'agent:lifecycle:orchestration:worker_reviewed': {
        // Both events fold into the worker's own moment, so one worker stays
        // one entry on the timeline instead of three.
        const workerId = asString(content.worker_id);
        const index = workerIndexById.get(workerId);
        if (index === undefined) break;
        const moment = moments[index];
        if (moment.payload.type !== 'worker') break;

        if (type.endsWith('worker_completed')) {
          moment.running = false;
          moment.payload.status = asString(content.status) || 'completed';
          moment.payload.summary = asString(content.summary);
          moment.detail = moment.payload.status;
        } else {
          const verdict = asString(content.verdict);
          moment.payload.verdict = verdict;
          moment.payload.reviewReason = asString(content.reason);
          moment.detail = verdict === 'accept' ? 'accepted' : 'rejected';
          if (verdict !== 'accept') moment.kind = 'error';
        }
        break;
      }

      case 'agent:lifecycle:orchestration:orchestration_joined': {
        const accepted = asNumber(content.accepted);
        const total = asNumber(content.total);
        moments.push({
          id,
          kind: accepted === total ? 'think' : 'error',
          at,
          title: `Reviewed ${total} result${total === 1 ? '' : 's'}`,
          detail: `${accepted} accepted, ${asNumber(content.rejected)} rejected`,
          running: false,
          payload: {
            type: 'note',
            text:
              accepted === total
                ? 'Every worker result was accepted. Synthesising the delivery.'
                : `${asNumber(content.rejected)} of ${total} results were rejected and excluded from the delivery.`,
            tone: accepted === total ? 'success' : 'error',
          },
        });
        break;
      }

      case 'agent:lifecycle:step:error':
      case 'agent:lifecycle:terminated': {
        const text =
          asString(content.message) || asString(content.reason) || 'The run stopped early';
        moments.push({
          id,
          kind: 'error',
          at,
          title: type.endsWith('terminated') ? 'Run stopped' : 'Step failed',
          detail: text.slice(0, 120),
          running: false,
          payload: { type: 'note', text, tone: 'error' },
        });
        break;
      }

      case 'agent:lifecycle:complete': {
        moments.push({
          id,
          kind: 'think',
          at,
          title: 'Run complete',
          detail: asString(content.message).slice(0, 120) || undefined,
          running: false,
          payload: {
            type: 'note',
            text: asString(content.message) || 'The agent finished this run.',
            tone: 'success',
          },
        });
        break;
      }

      default:
        break;
    }
  });

  return moments;
};
