package com.bank.mobile.domain;

import com.bank.mobile.infra.CardAdapterClient;
import org.springframework.stereotype.Service;

@Service
public class CardService {
    private final CardAdapterClient cardAdapter;

    public CardService(CardAdapterClient cardAdapter) { this.cardAdapter = cardAdapter; }

    public void block(String cardId, String reason) {
        cardAdapter.blockCard(cardId, reason);
    }
}
