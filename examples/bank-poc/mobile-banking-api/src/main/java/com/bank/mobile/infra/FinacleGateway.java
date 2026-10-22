package com.bank.mobile.infra;

import com.bank.mobile.domain.CoreBankingGateway;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.ws.client.core.WebServiceTemplate;

@Component
public class FinacleGateway implements CoreBankingGateway {
    private final WebServiceTemplate soap = new WebServiceTemplate();

    @Value("${finacle.url}")
    private String finacleUrl;

    @Override
    public String getBalance(String accountId) {
        Object response = soap.marshalSendAndReceive(finacleUrl, "<BalInq>" + accountId + "</BalInq>");
        return String.valueOf(response);
    }
}
