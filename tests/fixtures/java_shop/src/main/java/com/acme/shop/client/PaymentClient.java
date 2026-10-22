package com.acme.shop.client;

import com.acme.shop.service.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

@Component
public class PaymentClient {
    private final RestTemplate http = new RestTemplate();

    public void charge(Order order) {
        http.postForObject("https://payments.internal/charge", order, Void.class);
    }
}
