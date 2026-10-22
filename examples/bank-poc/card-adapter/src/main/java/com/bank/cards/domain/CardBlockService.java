package com.bank.cards.domain;

import com.bank.cards.infra.DcmsClient;
import com.bank.cards.infra.FssHotlistClient;
import org.springframework.stereotype.Service;

@Service
public class CardBlockService {
    private final DcmsClient dcms;
    private final FssHotlistClient fss;

    public CardBlockService(DcmsClient dcms, FssHotlistClient fss) {
        this.dcms = dcms;
        this.fss = fss;
    }

    public void block(String cardId, String reason) {
        dcms.blockCard(cardId);
        if ("STOLEN".equals(reason) || "LOST".equals(reason)) {
            fss.hotlist(cardId);
        }
    }
}
