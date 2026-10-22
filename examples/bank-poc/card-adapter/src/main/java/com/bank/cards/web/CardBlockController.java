package com.bank.cards.web;

import com.bank.cards.domain.CardBlockService;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/cards")
public class CardBlockController {
    private final CardBlockService service;

    public CardBlockController(CardBlockService service) { this.service = service; }

    @PostMapping("/{cardId}/block")
    public void block(@PathVariable String cardId, @RequestParam String reason) {
        service.block(cardId, reason);
    }
}
