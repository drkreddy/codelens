package com.bank.mobile.infra;

import com.bank.mobile.domain.AadhaarGateway;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;

@Component
public class UidaiAuthClient implements AadhaarGateway {
    private static final String AUTH_NS = "http://www.uidai.gov.in/authentication/uid-auth-request/2.0";
    private final WebClient webClient = WebClient.create();

    @Value("${uidai.auth.url}")
    private String authUrl;

    @Override
    public boolean authenticate(String aadhaarRef) {
        String xml = "<Auth xmlns=\"" + AUTH_NS + "\" uid=\"" + aadhaarRef + "\"/>";
        String result = webClient.post()
            .uri(authUrl + "/auth")
            .bodyValue(xml)
            .retrieve()
            .bodyToMono(String.class)
            .block();
        return result != null && result.contains("ret=\"y\"");
    }
}
