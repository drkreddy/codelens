package com.bank.mobile.infra;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;

@FeignClient(name = "card-adapter", url = "${services.card-adapter.url}")
public interface CardAdapterClient {
    @PostMapping("/api/cards/{cardId}/block")
    void blockCard(@PathVariable String cardId, @RequestParam String reason);
}
