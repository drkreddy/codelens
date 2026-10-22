package com.bank.payments.infra;

import com.bank.payments.domain.CoreBankingPort;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

@Component
public class EsbCoreBankingAdapter implements CoreBankingPort {
    private static final String DEBIT = "FIN_DEBIT_TXN";
    private static final String CREDIT = "FIN_CREDIT_TXN";
    private final RestTemplate rest = new RestTemplate();

    @Value("${esb.url}")
    private String esbUrl;

    @Override
    public void debit(String account, long amountPaise) {
        rest.postForObject(esbUrl + "/" + DEBIT, request(account, amountPaise), String.class);
    }

    @Override
    public void credit(String account, long amountPaise) {
        rest.postForObject(esbUrl + "/" + CREDIT, request(account, amountPaise), String.class);
    }

    private HttpEntity<String> request(String account, long amountPaise) {
        return new HttpEntity<>(account + ":" + amountPaise, new HttpHeaders());
    }
}
