package com.bank.cards.infra;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

@Component
public class FssHotlistClient {
    private final RestTemplate restTemplate = new RestTemplate();

    @Value("${fss.base-url}")
    private String baseUrl;

    public void hotlist(String cardId) {
        restTemplate.postForObject(baseUrl + "/hotlist", cardId, Void.class);
    }
}
