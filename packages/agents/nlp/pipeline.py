"""NLP pipeline orchestrator — the single entry point for message analysis.

Called by the SQS worker after a buyer message is received via webhook.
Runs intent classification, sentiment analysis, and entity extraction,
then persists results to the database.
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from packages.db.models import EntityMention, NlpAnnotation, OfferSignal
from packages.schemas.nlp import NlpResult

logger = logging.getLogger(__name__)


async def analyse_message(
    message_id: uuid.UUID,
    raw_text: str,
    seller_id: uuid.UUID,
    session: AsyncSession,
) -> NlpResult:
    """Run the full NLP pipeline on a buyer message.

    1. Zero-shot intent classification (DistilBART-MNLI)
    2. Sentiment analysis (RoBERTa)
    3. Entity extraction (spaCy NER + regex price patterns)

    Persists NlpAnnotation, OfferSignal, and EntityMention rows.
    Returns an NlpResult for Agent 4 consumption.
    """
    from packages.agents.nlp.entities import extract_entities, extract_price_offers
    from packages.agents.nlp.intent import classify_intent
    from packages.agents.nlp.sentiment import analyse_sentiment

    logger.info("NLP pipeline starting for message %s", message_id)

    # --- 1. Intent classification ---
    intent, intent_confidence = classify_intent(raw_text)

    # --- 2. Sentiment analysis ---
    sentiment, sentiment_score = analyse_sentiment(raw_text)

    # --- 3. Entity extraction ---
    entities = extract_entities(raw_text)
    offer_signals = extract_price_offers(raw_text)

    # --- 4. Persist NlpAnnotation ---
    annotation = NlpAnnotation(
        buyer_message_id=message_id,
        seller_id=seller_id,
        intent=intent,
        intent_confidence=intent_confidence,
        sentiment=sentiment,
        sentiment_score=sentiment_score,
        raw_output={
            "intent": intent,
            "intent_confidence": intent_confidence,
            "sentiment": sentiment,
            "sentiment_score": sentiment_score,
        },
    )
    session.add(annotation)

    # --- 5. Persist OfferSignals ---
    for signal in offer_signals:
        session.add(
            OfferSignal(
                buyer_message_id=message_id,
                seller_id=seller_id,
                amount=signal.amount,
                currency=signal.currency,
                source=signal.source,
            )
        )

    # --- 6. Persist EntityMentions ---
    for entity in entities:
        session.add(
            EntityMention(
                buyer_message_id=message_id,
                seller_id=seller_id,
                entity_type=entity.entity_type,
                entity_value=entity.entity_value,
                start_char=entity.start_char,
                end_char=entity.end_char,
            )
        )

    await session.flush()

    logger.info(
        "NLP pipeline complete for message %s: intent=%s sentiment=%s offers=%d entities=%d",
        message_id,
        intent,
        sentiment,
        len(offer_signals),
        len(entities),
    )

    return NlpResult(
        intent=intent,
        intent_confidence=intent_confidence,
        sentiment=sentiment,
        sentiment_score=sentiment_score,
        offer_amounts=[s.amount for s in offer_signals],
        entities=list(entities),
        offer_signals=list(offer_signals),
    )
