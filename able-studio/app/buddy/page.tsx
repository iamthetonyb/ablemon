"use client";

import useSWR from "swr";
import { Heart, Zap, Shield, Droplets, Battery, Trophy, Star } from "lucide-react";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const SPECIES_COLORS: Record<string, string> = {
  blaze: "text-orange-400",
  wave: "text-blue-400",
  root: "text-green-400",
  spark: "text-yellow-400",
  phantom: "text-purple-400",
  aether: "text-gold-400",
};

const SPECIES_LABELS: Record<string, string> = {
  blaze: "Blaze • Fire",
  wave: "Wave • Water",
  root: "Root • Earth",
  spark: "Spark • Electric",
  phantom: "Phantom • Shadow",
  aether: "Aether • Cosmos",
};

function NeedsBar({ label, value, icon: Icon, color }: {
  label: string;
  value: number;
  icon: React.ElementType;
  color: string;
}) {
  const pct = Math.round(Math.min(Math.max(value * 100, 0), 100));
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <div className={`flex items-center gap-1.5 ${color}`}>
          <Icon className="w-3.5 h-3.5" />
          <span className="font-medium">{label}</span>
        </div>
        <span className="text-text-muted font-mono">{pct}%</span>
      </div>
      <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${
            pct > 60 ? "bg-green-500" : pct > 30 ? "bg-yellow-500" : "bg-red-500"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function XpBar({ xp, xpToNext }: { xp: number; xpToNext: number }) {
  const pct = xpToNext > 0 ? Math.min((xp / xpToNext) * 100, 100) : 100;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-text-muted">XP Progress</span>
        <span className="text-gold-400 font-mono">{xp} / {xpToNext}</span>
      </div>
      <div className="h-2 bg-white/5 rounded-full overflow-hidden">
        <div
          className="h-full bg-gold-500 rounded-full transition-all duration-700"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default function BuddyPage() {
  const { data, error, isLoading, mutate } = useSWR("/api/buddy", fetcher, {
    refreshInterval: 15000,
    errorRetryCount: 3,
  });

  if (isLoading) {
    return (
      <div className="space-y-6 animate-fade-in">
        <div className="h-8 w-40 skeleton" />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="h-80 skeleton" />
          <div className="h-80 skeleton" />
        </div>
      </div>
    );
  }

  if (error || data?.error || !data?.buddy) {
    return (
      <div className="glass-card p-10 text-center">
        <div className="text-4xl mb-4">🥚</div>
        <p className="text-text-primary font-medium mb-2">No buddy yet</p>
        <p className="text-text-muted text-sm">
          Start an <code className="bg-white/10 px-1.5 py-0.5 rounded text-xs">able chat</code> session
          to choose your starter buddy.
        </p>
        {data?.error && (
          <p className="text-error text-xs mt-4 font-mono">{data.error}</p>
        )}
      </div>
    );
  }

  const buddy = data.buddy;
  const speciesColor = SPECIES_COLORS[buddy.species] ?? "text-gold-400";
  const speciesLabel = SPECIES_LABELS[buddy.species] ?? buddy.species;
  const totalBattles = buddy.wins + buddy.losses + buddy.draws;
  const winRate = totalBattles > 0 ? Math.round((buddy.wins / totalBattles) * 100) : 0;

  return (
    <div className="animate-fade-in">
      <div className="flex items-start justify-between mb-8">
        <div>
          <h2 className="text-2xl font-semibold text-white mb-1">{buddy.name}</h2>
          <p className={`text-sm ${speciesColor}`}>{speciesLabel}</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="glass-card px-3 py-1.5 text-xs text-text-muted font-mono">
            Stage {buddy.stage}
          </span>
          <button
            onClick={() => mutate()}
            className="glass-card px-3 py-1.5 text-xs text-gold-400 hover:text-gold-300 transition-colors"
          >
            Refresh
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left: Level + Needs */}
        <div className="space-y-4">
          {/* Level card */}
          <div className="glass-card-elevated p-6">
            <div className="flex items-center justify-between mb-5">
              <div>
                <p className="text-[11px] text-text-muted uppercase tracking-wider mb-1">Level</p>
                <p className="text-4xl font-bold text-gold-400">{buddy.level}</p>
              </div>
              <div className={`text-5xl ${speciesColor}`}>
                <Zap className="w-12 h-12 opacity-20" />
              </div>
            </div>
            <XpBar xp={buddy.xp} xpToNext={buddy.xp_to_next} />
          </div>

          {/* Needs card */}
          <div className="glass-card-elevated p-6">
            <h3 className="text-[11px] uppercase tracking-wider text-text-muted mb-5">Needs</h3>
            <div className="space-y-4">
              <NeedsBar label="Hunger" value={buddy.hunger} icon={Shield} color="text-orange-400" />
              <NeedsBar label="Thirst" value={buddy.thirst} icon={Droplets} color="text-blue-400" />
              <NeedsBar label="Energy" value={buddy.energy} icon={Battery} color="text-green-400" />
            </div>
            <div className="mt-5 pt-4 border-t border-border-subtle">
              <p className="text-[11px] text-text-muted uppercase tracking-wider mb-1">Mood</p>
              <p className="text-sm text-text-primary capitalize">{buddy.mood}</p>
            </div>
          </div>
        </div>

        {/* Right: Battle record + badges */}
        <div className="space-y-4">
          {/* Battle stats */}
          <div className="glass-card-elevated p-6">
            <div className="flex items-center gap-2 mb-5">
              <Trophy className="w-4 h-4 text-gold-400" />
              <h3 className="text-[11px] uppercase tracking-wider text-text-muted">Battle Record</h3>
            </div>
            <div className="grid grid-cols-3 gap-4 mb-4">
              <div className="text-center">
                <p className="text-2xl font-bold text-green-400">{buddy.wins}</p>
                <p className="text-[11px] text-text-muted mt-1">Wins</p>
              </div>
              <div className="text-center">
                <p className="text-2xl font-bold text-text-muted">{buddy.draws}</p>
                <p className="text-[11px] text-text-muted mt-1">Draws</p>
              </div>
              <div className="text-center">
                <p className="text-2xl font-bold text-red-400">{buddy.losses}</p>
                <p className="text-[11px] text-text-muted mt-1">Losses</p>
              </div>
            </div>
            {totalBattles > 0 && (
              <div className="pt-4 border-t border-border-subtle">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-text-muted">Win rate</span>
                  <span className="text-gold-400 font-mono">{winRate}%</span>
                </div>
                <div className="h-1.5 bg-white/5 rounded-full overflow-hidden mt-2">
                  <div
                    className="h-full bg-gold-500 rounded-full"
                    style={{ width: `${winRate}%` }}
                  />
                </div>
              </div>
            )}
            {totalBattles === 0 && (
              <p className="text-text-muted text-xs text-center pt-2">
                No battles yet. Use <code className="bg-white/10 px-1 rounded">/battle</code> in the CLI.
              </p>
            )}
          </div>

          {/* Badges */}
          <div className="glass-card-elevated p-6">
            <div className="flex items-center gap-2 mb-5">
              <Star className="w-4 h-4 text-gold-400" />
              <h3 className="text-[11px] uppercase tracking-wider text-text-muted">Badges</h3>
            </div>
            {buddy.badges && buddy.badges.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {buddy.badges.map((badge: string, i: number) => (
                  <span
                    key={i}
                    className="text-xs border border-gold-400/30 text-gold-400 px-2.5 py-1 rounded-full"
                  >
                    {badge}
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-text-muted text-xs">
                No badges yet. Earn them through battles and milestones.
              </p>
            )}
          </div>

          {/* Quick tips */}
          <div className="glass-card p-4">
            <p className="text-[11px] text-text-muted uppercase tracking-wider mb-3">How to care</p>
            <div className="space-y-1.5 text-xs text-text-secondary">
              <div className="flex items-center gap-2">
                <span className="text-orange-400">•</span>
                <span>Hunger: run <code className="bg-white/10 px-1 rounded">/battle</code> in the CLI</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-blue-400">•</span>
                <span>Thirst: run <code className="bg-white/10 px-1 rounded">/buddy feed water</code></span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-green-400">•</span>
                <span>Energy: run <code className="bg-white/10 px-1 rounded">/buddy feed walk</code></span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <p className="text-[11px] text-text-muted mt-6 text-right font-mono">
        Updated {buddy.timestamp ? new Date(buddy.timestamp).toLocaleTimeString() : "—"}
      </p>
    </div>
  );
}
