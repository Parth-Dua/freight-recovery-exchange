// Public, sanitized DTOs served by the read-only ops API (src/freight_exchange/ops). Never business rows.

export type ActorKind = "BUYER_AGENT" | "CARRIER_AGENT" | "EXCHANGE" | "CARRIER_OPS" | "PAYMENT_SERVICE" | "BOOKING_SERVICE";

export interface PublicMarketEvent {
  schemaVersion: 1;
  eventId: string;
  eventType: string;
  sourceType: string;
  eventTime: string;
  recordedAt: string;
  streamCursor: string;
  incidentId?: string | null;
  requestId?: string | null;
  sessionId?: string | null;
  bookingId?: string | null;
  holdId?: string | null;
  sailingId?: string | null;
  carrierId?: string | null;
  logicalBuyerId?: string | null;
  buyer?: string | null;
  sessionKind?: "BOOKING" | "RECOVERY" | null;
  corridorId?: string | null;
  actorKind: ActorKind;
  publicPayload: Record<string, any>;
  summary: string;
  sourceEventId?: string;
}

export interface PublicPort { portCode: string; name: string; country: string; lat: number; lon: number }
export interface PublicCorridor { corridorId: string; origin: string; destination: string; route: [number, number][] }
export interface PublicCarrier { carrierId: string; displayName: string; maskedAddress?: string | null; active: boolean; vessels: number; kind: string }

export interface PublicSailing {
  sailingId: string; carrierId: string; vesselId: string; vesselName?: string | null; corridorId: string;
  origin: string; destination: string; departureAt: string; arrivalAt: string;
  status: "ACTIVE" | "WITHDRAWN" | "UNKNOWN";
  totalUnits: number; committedUnits: number; heldUnits: number; availableUnits: number; withdrawnUnits: number;
  slotAvailableUnits?: number; containerType: "20GP"; positionKind: "SIMULATED";
}

export interface PublicHold { holdId: string; carrierId?: string; sailingId: string; quantity: number; status: string; expiresAt?: string | null; recovery?: boolean }

export interface PublicBooking {
  bookingId: string; buyer?: string | null; buyerNode?: string | null; carrierId: string; sailingId: string;
  quantity: number; status: string; totalMinor?: number | null; currency?: string; replacesBookingId?: string | null;
  createdAt?: string | null; confirmedAt?: string | null;
}

export interface PublicSession {
  sessionId: string; kind: "BOOKING" | "RECOVERY"; requestId?: string | null; bookingId?: string | null;
  incidentId?: string | null; buyer?: string | null; buyerNode?: string | null; carrierId: string;
  status: "OPEN" | "ACCEPTED" | "REJECTED" | "EXHAUSTED" | "EXPIRED"; round: number; roundsMax?: number;
  openedAt?: string | null; closedAt?: string | null; deadlineAt?: string | null;
  openingMinor?: number | null; finalMinor?: number | null; latestMinor?: number | null; lastCounterMinor?: number | null;
  finalSailingId?: string | null; finalQuantity?: number | null;
}

export interface NegotiationStep {
  eventId: string; at: string; senderKind: "BUYER_AGENT" | "CARRIER_AGENT";
  messageKind: "OPENING_OFFER" | "BUYER_COUNTER" | "CARRIER_COUNTER" | "ACCEPT" | "REJECT" | "NO_REPLY" | "EXPIRED";
  counterRound: number; offerId?: string | null; amountMinor?: number | null; proposed: boolean;
  explanationCode?: string | null; signedSenderVerified?: boolean | null;
}
export interface SessionDetail extends PublicSession { steps: NegotiationStep[]; termsComparableToOpening: boolean; eligibility: string }

export interface IncidentSummary {
  incidentId: string; sailingId: string; runStatus: string; stage: string; openedAt?: string | null; updatedAt?: string | null;
  affected: number; confirmed: number; unresolved: number; pending: number; attempts?: number;
}

export interface Projection {
  schemaVersion: number; asOf: string; cursor?: string | null; completeness: "COMPLETE" | "PARTIAL" | "STALE"; marketKind: string;
  ports: PublicPort[]; corridors: PublicCorridor[]; carriers: PublicCarrier[]; sailings: PublicSailing[];
  holds: PublicHold[]; bookings: PublicBooking[]; negotiations: PublicSession[]; incidents: IncidentSummary[];
  metrics: { activeCarriers: number; activeSailings: number; activeNegotiations: number; confirmedBookings: number; incidentsOpen: number };
}

export interface AffectedBooking {
  bookingId: string; buyer?: string | null; buyerNode?: string | null; quantity: number; previousSailingId?: string | null;
  previousTotalMinor?: number | null; bookingStatus?: string | null; deadlineAt?: string | null; state: string; reason?: string | null;
  approval?: { status?: string; expiresAt?: string; requestedAt?: string; decidedAt?: string } | null;
  replacement?: { bookingId: string; sailingId: string; carrierId: string; status: string; totalMinor?: number } | null;
  legs: { attempt: number; sailingId: string; carrierId: string; quantity: number; quotedMinor: number; status: string;
          needsApproval: boolean; holdId?: string | null; holdExpiresAt?: string | null; arrivalAt?: string | null;
          replacementBookingId?: string | null }[];
}

export interface IncidentDetail extends IncidentSummary {
  withdrawal?: { eventId: string; at: string; carrierId: string; carrierName: string; sailingId: string; authorizedBy: string; reason: string } | null;
  affectedBookings: AffectedBooking[]; unaffectedCommitted: number;
  board: { jobRef: string; quantity: number; allowSplit: boolean; origin: string; destination: string; latestArrivalAt?: string | null }[];
  negotiation: { deadlineAt?: string | null; closedAt?: string | null; timeoutSeconds: number; sessions: number;
                 byStatus: Record<string, number>; closedEarly?: boolean | null; seconds?: number | null };
  clearing?: {
    at?: string | null; eventId?: string | null; inputVersion?: string; offersValid?: number;
    objective?: { mustDeliverRecovered: number; recovered: number; approvals: number; incrementalCostMinor: number; arrivalRank: number } | null;
    joint: { recovered: number; assignments: { bookingId: string; sailingId: string; quantity: number }[]; needsApproval: string[]; unrecovered: string[] };
    baseline: { kind?: string; recovered: number; order?: string[]; assignments: { bookingId: string; sailingId: string; quantity: number }[]; unrecovered: string[] };
  } | null;
  stages: { stage: string; at: string; eventId: string }[];
  sessionsList: PublicSession[];
}

export interface ReplayBundle {
  schemaVersion: number; title: string; incidentId?: string | null;
  checkpoint: { snapshotId: string; capturedAt: string; cursor?: string | null; reason: string; projection: Projection } | null;
  events: PublicMarketEvent[];
  coverage: { start: string; end: string; complete: boolean; reason?: string | null };
  cursor?: string | null;
  sessions: SessionDetail[];
  reference: { asOf: string; terminal: boolean; sailings: Pick<PublicSailing, "sailingId" | "status" | "committedUnits" | "heldUnits" | "availableUnits">[];
               bookings: Pick<PublicBooking, "bookingId" | "status">[] };
}

export interface Decision {
  kind: string; id: string; title: string; status: string; asOf: string; what: string; why?: string | null;
  facts: [string, string][]; evidence: { label: string; value: string; eventId?: string; at?: string }[];
  links: { kind: string; label: string; incidentId?: string; sessionId?: string; at?: string }[];
}

export interface Provenance {
  metric?: string; queryId?: string; sql?: string; grain?: string; source?: string; rows?: number; valueKind?: string;
  watermark?: string | null; sourceTables?: string[]; start?: string; end?: string; filters?: Record<string, string>;
  aggregate?: any; materializedRows?: number; rawTailRows?: number; rawEventCount?: number; bucketedEventCount?: number;
}

export type InspectTarget =
  | { kind: "sailing"; id: string } | { kind: "session"; id: string } | { kind: "booking"; id: string }
  | { kind: "incident"; id: string } | { kind: "event"; id: string } | { kind: "provenance"; provenance: Provenance; title: string };
