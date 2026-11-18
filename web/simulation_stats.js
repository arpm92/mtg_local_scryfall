(function exposeSimulationStats(root) {
  'use strict';

  function winnerFrom(line) {
    const explicit = line.match(/\bwinner=(.+?)\s*$/i);
    if (explicit) return explicit[1].trim();
    const legacy = line.match(/(?:Game Outcome:\s*)?(.+?)\s+has won\b/i);
    return legacy ? legacy[1].trim() : '';
  }

  function summarize(job) {
    const participants = [...new Set((job?.players_in_seat_order || []).filter(Boolean))];
    if (!participants.length) {
      [job?.deck, ...(job?.opponents || [])].filter(Boolean).forEach(name => {
        if (!participants.includes(name)) participants.push(name);
      });
    }
    const wins = new Map(participants.map(name => [name, 0]));
    let draws = 0;
    let unresolvedReported = 0;
    let completedReported = 0;

    (job?.game_results || []).forEach(line => {
      const normalWin = /\bstatus=NORMAL_WIN\b/i.test(line) || /\bhas won\b/i.test(line);
      const normalDraw = /\bstatus=NORMAL_DRAW\b/i.test(line) || /\bhas drawn\b/i.test(line);
      if (!normalWin && !normalDraw) return;
      completedReported += 1;
      if (normalDraw) {
        draws += 1;
        return;
      }
      const winner = winnerFrom(line);
      if (wins.has(winner)) wins.set(winner, wins.get(winner) + 1);
      else unresolvedReported += 1;
    });

    const reportedCompleted = Number(job?.completed_games);
    const completed = Math.max(Number.isFinite(reportedCompleted) ? reportedCompleted : 0, completedReported);
    const attemptedValue = Number(job?.attempted_games ?? job?.games_requested);
    const attempted = Math.max(Number.isFinite(attemptedValue) ? attemptedValue : completed, completed);
    const unresolved = Math.max(unresolvedReported, completed - [...wins.values()].reduce((sum, value) => sum + value, 0) - draws);
    const decided = Math.max(0, completed - draws - unresolved);

    return {
      attempted,
      completed,
      draws,
      unresolved,
      players: participants.map(name => {
        const playerWins = wins.get(name) || 0;
        return {
          name,
          wins: playerWins,
          losses: Math.max(0, decided - playerWins),
          draws,
          winRate: completed ? Math.round((playerWins / completed) * 1000) / 10 : null,
        };
      }),
    };
  }

  const api = {summarize};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.MtgSimulationStats = api;
}(typeof globalThis !== 'undefined' ? globalThis : this));
