package com.bank.mobile.web;

import com.bank.mobile.domain.CardService;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/cards")
public class CardController {
    private final CardService cards;

    public CardController(CardService cards) { this.cards = cards; }

    @PostMapping("/{id}/block")
    public void block(@PathVariable String id) {
        cards.block(id, "LOST");
    }
}
